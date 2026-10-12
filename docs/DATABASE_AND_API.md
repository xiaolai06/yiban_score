# V2 数据库、清洗模块与 API

## 1. 核心关系

```text
colleges (学院) 1 ── N classes (班级) 1 ── N students (学号为 TEXT 主键)
                                                     │
                      当前来源 score_batch_id ───────┘
score_batches (成绩导入批次) 1 ── N score_import_rows (逐行原值/清洗值/结果)
student_changes (每次学生变更的 before/after 审计)
import_batches (原名单导入批次，保留 V1 功能)
metadata (schema_version=2 / revision)
```

`score_import_rows.student_id` 故意不设学生外键，因为必须保存原名单外学号的异常行；这不代表可以新建该学生。`students.class_id`、学院班级关系仍是正式外键。

students 保留所有 V1 列；新增：

| 列 | 类型/含义 |
|---|---|
| score_batch_id | 可空整数，当前成绩来源成绩批次 |
| score_imported_at | 可空 UTC 时间，当前成绩导入时间 |
| status | CHECK 扩为 7 种状态 |
| score_rate | 原有 REAL NULL，0–100；NULL 不等于 0 |
| version | 每次实际编辑或导入更新递增，用于并发/撤销保护 |

时间在数据库按 UTC ISO 文本保存；前端按浏览器时区显示。批次记录含规则快照、文件 SHA-256、统计、原文件名与编码。不是多场考试的业务成绩表。

## 2. 读取与清洗函数

`server/score_import.py`：

- `read_score_file(data: bytes, filename: str)`：读取 CSV/TSV 或委托 XLS/XLSX；保留 Excel 单元格类型/格式，不执行公式。
- `score_sheet_info(sheet, index, header_row=None)`：识别表头、映射与可供预览的内容。
- `clean_identifier(cell)`：返回学号及变更说明，遇到不可靠数据抛出 ValueError。
- `clean_status(raw: str)`：状态标准化，未知值不猜测。
- `clean_rate(cell, scale='percent')`：Decimal 解析、有界、有限、两位小数；缺失返回 None。
- `result_rate(cell)`：只抽取明确的百分数，不推算满分。
- `build_preview(sheet, header_row, mapping, existing, ...)`：不写数据库；逐行分类，重复检测，返回 summary/rows/warnings。

默认列映射字段：`student_id, name, status, score_rate, result_text, college, class_name`，索引从 0 开始。学号、状态必需，得分率/成绩百分比至少选一个；姓名/学院/班级仅核对。单个源列不能映射为互相冲突的多个用途。

限制：上传 20 MB；最多 60,000 行、160 列与 1,500,000 单元格。CSV 逐条记录解析，字段内换行支持；`row_number` 是 1 起始的逻辑记录号（含表头/空记录等），不保证等于文本编辑器的物理换行行号。CSV 行与表头列数不同的记录列为异常。

## 3. HTTP 约定

本机模式 Base URL：`http://127.0.0.1:8000`。云服务器模式通过专用域名 `https://<新域名>/` 访问；部署步骤见 `deploy/README.md`。

所有写请求必须带：`X-Requested-With: StudentScore`。JSON 请求使用 `Content-Type: application/json`。浏览器的 Origin 若存在，必须与请求 origin 相同。本机模式拒绝其他 host/client；云服务器模式只接受配置域名的 HTTPS 请求，公网访问还由 Nginx IP 白名单限制。错误返回 JSON `detail`，前端显示具体原因。

请求示例使用 Python（`httpx` 是开发/测试依赖，运行网站本身无需）：

```python
from pathlib import Path
import httpx

path = Path('scores.csv')
with httpx.Client(base_url='http://127.0.0.1:8000',
                  headers={'X-Requested-With': 'StudentScore'}, timeout=60) as c:
    with path.open('rb') as f:
        response = c.post('/api/scores/analyze', files={'file': (path.name, f)})
    response.raise_for_status()
    upload = response.json()
    sheet = upload['sheets'][upload['selected_sheet']]
    response = c.post('/api/scores/preview', json={
        'upload_id': upload['upload_id'],
        'sheet_index': upload['selected_sheet'],
        'header_row': sheet['header_row'],
        'mapping': sheet['mapping'],
        'batch_name': '2026 入学测验',
        'scale': 'percent',
        'zero_policy': 'null_unfinished',
        'update_mode': 'keep_highest',
        'protect_completed': True,
    })
    response.raise_for_status()
    preview = response.json()
    print(preview['summary'])
    # 此处只生成预览，不自动确认。不应跳过用户核对而默认接受所有异常。
```

## 4. 成绩接口

| 方法/路径 | 用途 |
|---|---|
| POST `/api/scores/analyze` | multipart 的 file；返回 upload_id、文件摘要、编码、工作表、映射 |
| GET `/api/scores/uploads/{upload_id}/sheet?sheet_index=0&header_row=1` | 手动换表头后重新识别 |
| POST `/api/scores/preview` | 创建服务端不可变预览；初次返回前 20 行 |
| GET `/api/scores/previews/{preview_id}/rows?kind=all&page=1&page_size=20` | 查询全部/异常/某处理类别；每页最多 100 |
| GET `/api/scores/previews/{preview_id}/export.csv?kind=all` | 提交前导出清洗记录；kind=issues 导出所有异常 |
| POST `/api/scores/commit` | 确认；带 preview_id、skip_issues；仅异常批次还需 save_issues_only=true（两个确认开关均默认 false） |
| GET `/api/scores/batches?page=1&page_size=10` | 全部持久历史；每页最多 50，无总量截断 |
| GET `/api/scores/batches/{id}/rows?kind=all&page=1&page_size=20` | 已提交批次的逐行快照 |
| GET `/api/scores/batches/{id}/export.csv?kind=all` | 从持久审计导出 |
| POST `/api/scores/batches/{id}/revert` | JSON `{ "confirm": true }`，整批受保护撤销 |
| GET `/api/scores/issues?q=&kind=all&page=1&page_size=20` | 历次异常按有效学号汇总，含最高记录得分率、出现数、批次数和来源；每页最多100 |
| GET `/api/scores/issues/occurrences?key=student:0099&page=1` | 一个汇总项的全部出现记录，按页返回；无效学号使用独立 row:批次:原表行 标识 |
| GET `/api/scores/issues/export.csv?scope=summary` | 导出当前 q / kind 的完整汇总；scope=occurrences 导出全部出现明细，key 可限定一个汇总项 |
| GET `/api/scores/issues/export.xlsx?scope=summary` | 同样筛选范围的 Excel 导出，学号按文本保存，源文字不能执行公式 |

preview JSON 参数：

| 字段 | 默认/取值 |
|---|---|
| upload_id | 必填，服务器返回的令牌 |
| sheet_index / header_row | 0 / 1；sheet 从 0 开始，行从 1 开始 |
| mapping | 必填，字段名→源列的 0 起始索引 |
| batch_name | 必填，非空，最大 120 字符 |
| scale | percent / fraction，默认 percent |
| zero_policy | null_unfinished / keep，默认前者 |
| update_mode | keep_highest（默认）；显式 replace / fill_missing 仅保留兼容已有集成 |
| protect_completed | true |

分类：update, unchanged, protected, unmatched, invalid, name_conflict, duplicate, duplicate_conflict。
`issues` 集合：unmatched / invalid / name_conflict / duplicate_conflict。

preview summary 的 `source_rows` 是除空记录和重复表头后被处理的记录条数；`matched_students` 是唯一匹配学号数；`placeholder_zero_rows` 包含未匹配行中识别到的占位 0；它不是实际写入 NULL 的人数。`new_scores` 为原得分率为空的新增 / 补录数，`improved_scores` 为分数提高数，`status_updates` 为相同分数下登记状态更完整的更新数。

`keep_highest` 不以导入顺序决定覆盖：未录入的成绩补入，较高得分率更新，较低 / 空值保留，相同分数保持来源，只有状态更完整时更新。同一学号的有效重复行按较高成绩合并为一条更新，其余行记为 duplicate 并保留原值；默认优先完成状态。任何非法 / 身份冲突的重复组整组列为 duplicate_conflict；相同最高分但不可判定的最终状态也不猜测。

每次确认导入（包括零更新）均持久保存批次及全部处理行；只有临时上传和预览会清理。历史分页返回全部记录的 total，撤销仅改变批次状态，不删除记录。导出追加“导入前得分率(%)”和“合并后得分率(%)”两列，异常行不显示为已写入的结果。

commit 流程：检查令牌→已提交令牌幂等返回→确认异常处理策略→BEGIN IMMEDIATE→检查 revision→写批次和逐行审计→仅更新 action=update 的学生→递增 revision→commit→清理本次临时上传。HTTP 409 表示需要修正/确认或重新预览。预览额外返回 can_save_issues；全无可处理成绩但有异常时，只有 skip_issues=true 且 save_issues_only=true 才会保存零更新异常批次。混合有正常成绩的批次不接受 save_issues_only=true，避免把正常写入误称为仅归档。

异常汇总是对 score_import_rows 与 score_batches 的只读投影，包括已撤销批次及旧版本已经保存的异常行，无需新表或数据迁移。同一有效学号合并，合法 0–100 得分率取最大值（0 与空不同，非法值不参与），相同分数保留较早最高分来源；每条出现行独立保留，occurrence_count 数行，batch_count 数不同批次。缺失 / 无效学号按批次与原表行独立分组，姓名冲突保留各姓名而不猜测正确身份。

q / kind 先筛选汇总项，再返回它们完整的最高值与历史，不因为分类筛选而丢掉其他批次的更高记录。导出不限页面，summary 输出人员汇总，occurrences 输出原学号、原姓名、原分数、状态、批次、时间与原因；异常数据从不写入正式学生成绩。

revert 流程：检查每个更新学生的当前 version 和 score_batch_id 与本批 after_version 一致；任一冲突返回 409；全部一致才事务还原原状态/得分率及来源，并增加当前版本，标记批次 reverted，保留审计。重复撤销同一已撤销批次幂等返回。

## 5. 保留的 V1 接口

| 方法/路径 | 用途 |
|---|---|
| GET `/api/health` | SQLite 状态、schema_version、状态枚举、限制；directory_api_version ≥3 表示不合格包含无有效成绩 |
| GET `/api/stats` | 人数/状态/学院汇总；新增全校及 colleges_detail 每学院 qualified（非空得分率 ≥90%，含90，不按状态排除），原有效平均字段兼容保留 |
| GET `/api/hierarchy` | 学院与班级树 |
| GET `/api/students` | 分页、q 搜索、college_id/class_id、status、score_filter（all/recorded/missing/below_90）、排序（含college_name/class_name）；返回完整筛选 qualified（有效>90）、below_90（不合格：<90或无有效成绩）、at_90（有效=90）和有效平均 |
| GET `/api/students/{student_id}` | 单个学生当前档案 |
| PATCH `/api/students/{student_id}` | JSON version/status/score_rate；实际修改递增版本并清空当前成绩来源 |
| GET `/api/students/export.csv` | 当前筛选的全部学生导出（兼容原接口） |
| POST `/api/students/export.xlsx` | JSON all_students/college_ids/class_ids 或 student_ids / student_filter + excluded_student_ids，below_90 可限定不合格；按真实班级ID分工作表 |
| GET `/api/database/backup` | 用 SQLite online backup 生成可下载数据库快照 |
| POST `/api/imports/analyze` | 原名单 XLS/XLSX 上传 |
| POST `/api/imports/preview` | 原名单预览 |
| POST `/api/imports/commit` | 原名单确认，不覆盖既有成绩 |

完整参数以 `/api/openapi.json` 或 `server/app.py` 为准。原有路由与前端对接未移除。

XLSX 范围导出示例：`{"college_ids":[1],"class_ids":[8,9],"below_90":false}`。学院和班级取并集，重复/重叠不重复导出；`all_students:true` 表示全校。须明确指定非空范围，未知学院/班级返回404。学院/班级范围导出不接受分页、搜索或状态筛选，以免截断整个班级。学生导出可用 `student_ids:["0001","0002"]` 指定本人；表头全选用 `student_filter:{q:"",college_id:1,class_id:null,status:"",score_filter:"below_90"}` 指定完整筛选，再通过 `excluded_student_ids` 排除取消勾选的人员，不限制当前页。学生与学院/班级选择不得混用，避免意外扩大导出范围；所有学号都作为绑定参数，未知指定学号返回404。`below_90:true` 与 `score_filter=below_90` 保留原参数名，但按最新不合格名单规则处理：`score_rate IS NULL OR status IN ('未开始','进行中') OR score_rate < 90`。OR条件整体加括号后与范围/学号/搜索条件相交，不会扩大全校范围。任何状态的空值都计入，未完成记录即使有暂存90/100分也计入；有有效成绩的恰好90分仍不包含。`below_90` 人数取同一完整快照的 total − qualified − at_90。有效成绩人数/平均值仍排除空值及未开始/进行中，原状态、空值和暂存分数原样保留。空结果返回400，不生成误导的空文件。

前端在不合格导出前核对 `/api/health` 的 directory_api_version，旧v2虽然有导出接口，但会漏掉无成绩人员，须重启服务加载v3；不允许把旧名单静默当成完整名单。筛选加载也核对版本，刷新可恢复已升级的服务，无需更换数据库。

导出使用 Python 标准库生成 XLSX，无额外运行依赖；每个班级一个工作表，自动处理同名、非法字符和31字长度限制。学号、姓名等字段为纯文本单元格（含以 `=` 开头的内容也不执行公式）；得分率使用百分比数值格式，得分为原0–100数值，空值留空。请求仍受对应访问边界、Origin与 `X-Requested-With: StudentScore` 检查，导出不写数据库。

## 6. 扩展多考试时不要复用批次代替考试

建议未来新增 `exams(id,name,date,total_score,...)` 和 `student_exam_scores(exam_id,student_id,status,raw_score,score_rate,version,...)`，唯一约束 `(exam_id,student_id)`；成绩批次关联 exam_id。这是未来设计建议，当前包未实现这些表和页面。

## 7. 参考实现依据

- Python CSV 官方文档：https://docs.python.org/3/library/csv.html
- Python sqlite3 官方文档：https://docs.python.org/3/library/sqlite3.html
- SQLite ALTER TABLE 与通用迁移流程：https://www.sqlite.org/lang_altertable.html
- FastAPI 文件上传：https://fastapi.tiangolo.com/tutorial/request-files/

这些是库机制参考；本项目清洗分类、业务语义、零分策略与测试结果来自本项目代码和附件验证，并非官方库对学校成绩的定义。
