"""Validated temporary import records and best-effort post-commit cleanup."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path

from fastapi import HTTPException

logger = logging.getLogger(__name__)
FIELDS = {
    '.upload.json': {'filename': str, 'sha256': str},
    '.score-upload.json': {'filename': str, 'sha256': str, 'format': dict},
    '.preview.json': {'upload_id': str, 'meta': dict, 'revision': int, 'summary': dict,
                      'rows': list, 'errors': list, 'name_conflicts': list, 'mode': str},
    '.score-preview.json': {'upload_id': str, 'meta': dict, 'revision': int, 'summary': dict,
                            'rows': list, 'options': dict, 'format': dict, 'batch_name': str},
    '.removal-preview.json': {'upload_id': str, 'meta': dict, 'revision': int,
                              'summary': dict, 'rows': list, 'errors': list},
}


def reject_constant(value):
    raise ValueError('Non-finite JSON number: '+value)


def unlink_quietly(path: Path) -> bool:
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError:
        # Never expose student fields or capability tokens in cleanup logs.
        logger.warning('临时文件清理失败；已提交的数据库结果保持有效，后续过期清理会重试。')
        return False


def read_stage_record(path: Path, suffix: str, ttl: int) -> dict:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'), parse_constant=reject_constant)
    except FileNotFoundError as exc:
        raise HTTPException(404, '导入预览不存在，请重新上传或生成预览。') from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(400, '临时导入记录无法读取，请重新上传。') from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, '临时导入记录格式异常，请重新上传。')
    created = payload.get('created_at')
    if type(created) not in (int, float) or not 0 <= created <= 253402300799:
        raise HTTPException(400, '临时导入记录时间无效，请重新上传。')
    if time.time() - created > ttl:
        unlink_quietly(path)
        raise HTTPException(410, '导入预览已过期（有效期 60 分钟），请重新生成。')
    if any(type(payload.get(key)) is not kind for key, kind in FIELDS.get(suffix, {}).items()):
        raise HTTPException(400, '临时导入记录字段不完整，请重新生成预览。')
    if 'revision' in payload and payload['revision'] < 0:
        raise HTTPException(400, '临时导入记录版本无效，请重新生成预览。')
    if 'upload_id' in payload and not re.fullmatch(r'[0-9a-f]{32}', payload['upload_id']):
        raise HTTPException(400, '临时上传标识无效，请重新上传。')
    for key in ('rows', 'errors', 'name_conflicts'):
        if key in FIELDS.get(suffix, {}) and any(not isinstance(row, dict) for row in payload[key]):
            raise HTTPException(400, '临时导入记录行格式异常，请重新生成预览。')
    return payload


def read_upload_bytes(path: Path, meta: dict) -> bytes:
    filename, digest = meta.get('filename'), meta.get('sha256')
    if not isinstance(filename, str) or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise HTTPException(400, '临时上传记录格式异常，请重新上传。')
    try:
        with path.open('rb') as source:
            data = source.read(20 * 1024 * 1024 + 1)
    except FileNotFoundError as exc:
        raise HTTPException(404, '临时文件已清理，请重新上传。') from exc
    except OSError as exc:
        raise HTTPException(400, '临时上传文件无法读取，请重新上传。') from exc
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, '临时上传文件超过20 MB，请重新上传。')
    if hashlib.sha256(data).hexdigest() != digest:
        raise HTTPException(409, '临时上传文件内容已变化，请重新上传；未修改任何数据。')
    return data


def cleanup_upload(stage: Path, upload_id: str, source_suffixes, preview_suffix: str) -> None:
    for suffix in source_suffixes:
        unlink_quietly(stage / (upload_id+suffix))
    for path in stage.glob('*'+preview_suffix):
        try:
            saved = json.loads(path.read_text(encoding='utf-8'), parse_constant=reject_constant)
            if isinstance(saved, dict) and saved.get('upload_id') == upload_id:
                unlink_quietly(path)
        except (OSError, ValueError):
            # Other sessions' malformed previews must not mask this committed result.
            continue
