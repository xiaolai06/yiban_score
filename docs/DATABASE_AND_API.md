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
removal_batches (剔除批次汇总) 1 ── N excluded_students (最小剔除学号标记)
removal_batches N ── N score_batches (通过 removal_score_batches 禁止被清理批次整批撤销)
```

`score_import_rows.student_id` 故意不设学生外键，因为必须保存原名单外学号的异常行；这不代表可以新建该学生。`students.class_id`、学院班级关系仍是正式外键。

students 保留所有 V1 列；新增：

| 列 | 类型/含义 |
|---|---|
| score_batch_id | 可空整数，当前成绩来源成绩批次 |
| score_imported_at | 可空 UTC 时间，当前成绩导入时间 |
| exam_started_at / exam_finished_at | 可空 ISO 文本，源报表开始/结束时间；保留源时区语义，不猜测时区 |
| answer_duration_seconds | 可空非负整数，结束减开始的秒数；NULL不等于0 |
| status | CHECK 扩为 7 种状态 |
| score_rate | 原有 REAL NULL，0–100；NULL 不等于 0 |
| version | 每次实际编辑或导入更新递增，用于并发/撤销保护 |

系统导入/修改时间按 UTC ISO 文本保存，前端按浏览器时区显示；考试开始/结束时间保留原报表时区语义、按原时刻显示，不强加UTC。批次记录含规则快照、文件 SHA-256、统计、原文件名与编码。不是多场考试的业务成绩表。

## 2. 读取与清洗函数

`server/score_import.py`：

- `read_score_file(data: bytes, filename: str)`：读取 CSV/TSV 或委托 XLS/XLSX；保留 Excel 单元格类型/格式，不执行公式。
- `score_sheet_info(sheet, index, header_row=None)`：识别表头、映射与可供预览的内容。
- `clean_identifier(cell)`：返回学号及变更说明，遇到不可靠数据抛出 ValueError。
- `clean_status(raw: str)`：状态标准化，未知值不猜测。
- `clean_rate(cell, scale='percent')`：Decimal 解析、有界、有限、两位小数；缺失返回 None。
- `result_rate(cell)`：只抽取明确的百分数，不推算满分。
- `build_preview(sheet, header_row, mapping, existing, ...)`：不写数据库；逐行分类，重复检测，返回 summary/rows/warnings。

默认列映射字段：`student_id, name, status, score_rate, result_text, college, class_name, exam_started_at, exam_finished_at, yiban_id`，索引从 0 开始。学号、状态必需，得分率/成绩百分比至少选一个；开始/结束时间始终选填、不提供也能导入；姓名/学院/班级仅核对。单个源列不能映射为互相冲突的多个用途。

限制：上传 20 MB；最多 60,000 行、160 列与 1,500,000 单元格。CSV 逐条记录解析，字段内换行支持；`row_number` 是 1 起始的逻辑记录号（含表头/空记录等），不保证等于文本编辑器的物理换行行号。CSV行与表头列数不同通常列为异常；仅省略末尾可识别且未被映射为身份/分数字段的可选时间单元格时，按缺失处理，不影响成绩导入。中间错位、遗漏成绩/学号或多余列仍拒绝。

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
| GET `/api/scores/batches/{id}/rows/{row_number}` | 精确读取指定批次的原表逻辑行，返回 batch/row，旧记录不补造 source_row；已剔除或不存在返回404 |
| GET `/api/scores/people?q=&status=&page=1&page_size=20` | 当前正式名单及成绩、historical_yiban_ids/qualified/settled/revision/pass_score；只读，最多100条/页，状态单独筛选 |
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

preview summary 的 `source_rows` 是除空记录和重复表头后被处理的记录条数；`matched_students` 是唯一匹配学号数；`placeholder_zero_rows` 包含未匹配行中识别到的占位 0；它不是实际写入 NULL 的人数。`new_scores` 为原得分率为空的新增 / 补录数，`improved_scores` 为分数提高数，`status_updates` 为相同分数下登记状态更完整的更新数；`time_updates`为实际更新时间字段的记录数（可与分数/状态更新交叉），单纯补齐时长不算状态更新。

`keep_highest` 不以导入顺序决定覆盖：未录入的成绩补入，较高得分率更新，较低 / 空值保留，相同分数保持来源，状态更完整或同分同状态缺失的完整时间可补齐时更新。同一学号的有效重复行按较高成绩合并为一条更新，其余行记为 duplicate 并保留原值；默认优先完成状态。任何非法 / 身份冲突的重复组整组列为 duplicate_conflict；相同最高分但不可判定的最终状态也不猜测。

每次确认导入（包括零更新）均持久保存批次及全部处理行；只有临时上传和预览会清理。历史分页返回全部记录的 total，撤销仅改变批次状态，不删除记录。导出追加“导入前得分率(%)”和“合并后得分率(%)”两列，异常行不显示为已写入的结果。

commit 流程：检查令牌→已提交令牌幂等返回→确认异常处理策略→BEGIN IMMEDIATE→检查 revision→写批次和逐行审计→仅更新 action=update 的学生→递增 revision→commit→清理本次临时上传。HTTP 409 表示需要修正/确认或重新预览。预览额外返回 can_save_issues；全无可处理成绩但有异常时，只有 skip_issues=true 且 save_issues_only=true 才会保存零更新异常批次。混合有正常成绩的批次不接受 save_issues_only=true，避免把正常写入误称为仅归档。

异常汇总是对 score_import_rows 与 score_batches 的只读投影，包括已撤销批次及旧版本已经保存的异常行，无需新表或数据迁移。同一有效学号合并，合法 0–100 得分率取最大值（0 与空不同，非法值不参与），相同分数保留较早最高分来源；每条出现行独立保留，occurrence_count 数行，batch_count 数不同批次。缺失 / 无效学号按批次与原表行独立分组，姓名冲突保留各姓名而不猜测正确身份。

q / kind 先筛选汇总项，再返回它们完整的最高值与历史，不因为分类筛选而丢掉其他批次的更高记录。导出不限页面，summary 输出人员汇总，occurrences 输出原学号、原姓名、原分数、状态、批次、时间与原因；异常数据从不写入正式学生成绩。

revert 流程：检查每个更新学生的当前 version 和 score_batch_id 与本批 after_version 一致；任一冲突返回 409；全部一致才事务还原原状态/得分率及来源，并增加当前版本，标记批次 reverted，保留审计。重复撤销同一已撤销批次幂等返回。

## 5. 保留的 V1 接口

| 方法/路径 | 用途 |
|---|---|
| GET `/api/health` | SQLite 状态、schema_version、状态枚举、限制；directory_api_version=6、pass_score=60；全站有效数值≥60合格、进度单独跟进 |
| GET `/api/stats` | 人数/状态/学院汇总；全校及 colleges_detail 每学院 qualified（有效得分率≥60，含60，不按状态排除）、completed（有正式数值成绩，含0且非未开始/进行中）；pass_score=60，原有效平均字段兼容保留 |
| GET `/api/hierarchy` | 学院与班级树 |
| GET `/api/students` | 分页、q 搜索、college_id/class_id、status、score_filter（all/recorded/missing/below_60）、排序（含college_name/class_name）；返回完整筛选 qualified（有效数值≥60）、below_60（<60或无有效数值）、at_60（数值=60，合格子集）、completed和已结算平均；below_90筛选/计数字段仅作旧调用别名，at_90仍只表示真实90分数量，不作为及格线 |
| GET `/api/students/{student_id}` | 单个学生当前档案 |
| PATCH `/api/students/{student_id}` | JSON version/status/score_rate；实际修改递增版本并清空当前成绩来源 |
| GET `/api/students/export.csv` | 当前筛选的全部学生导出（兼容原接口） |
| POST `/api/students/export.xlsx` | JSON all_students/college_ids/class_ids 或 student_ids / student_filter + excluded_student_ids，below_60 可限定不合格（below_90仅为兼容别名）；按真实班级ID分工作表 |
| GET `/api/database/backup` | 用 SQLite online backup 生成可下载数据库快照 |
| POST `/api/imports/analyze` | 原名单 XLS/XLSX 上传 |
| POST `/api/imports/preview` | 原名单预览 |
| POST `/api/imports/commit` | 原名单确认，不覆盖既有成绩 |

完整参数以 `/api/openapi.json` 或 `server/app.py` 为准。原有路由与前端对接未移除。

XLSX 范围导出示例：`{"college_ids":[1],"class_ids":[8,9],"below_60":false}`。学院和班级取并集，重复/重叠不重复导出；`all_students:true` 表示全校。须明确指定非空范围，未知学院/班级返回404。学院/班级范围导出不接受分页、搜索或状态筛选，以免截断整个班级。学生导出可用 `student_ids:["0001","0002"]` 指定本人；表头全选用 `student_filter:{q:"",college_id:1,class_id:null,status:"",score_filter:"below_60"}` 指定完整筛选，再通过 `excluded_student_ids` 排除取消勾选的人员，不限制当前页。学生与学院/班级选择不得混用，避免意外扩大导出范围；所有学号都作为绑定参数，未知指定学号返回404。新页面使用`below_60:true`及`score_filter=below_60`；旧`below_90`仅为相同不合格规则的兼容别名，两个布尔开关任一为true即限定不合格。规则为`NOT QUALIFIED_SQL`（`server/grade_rules.py`的PASS_SCORE=60，有效数值60–100即合格）。与范围/学号/搜索条件相交，不会扩大全校范围。任何状态的空值/无有效数值或<60均不合格；≥60的未完成记录分数已合格，另列待跟进，不进入低分不合格文件。`below_60`为同一快照的total−qualified；`at_60`是qualified子集，不能重复扣除。结算人数/平均值仍排除空值及未开始/进行中，原状态、空值和暂存分数保持不变。空结果返回400，不生成误导的空文件。

前端在不合格导出前核对 `/api/health` 的 directory_api_version≥6，旧版本存在规则或字段差异，须重启服务；不允许静默下载旧规则名单。筛选加载也核对版本，刷新可恢复已升级的服务，无需更换数据库。

导出使用 Python 标准库生成 XLSX，无额外运行依赖；每个班级一个工作表，最后附加「导出说明」工作表，自动处理同名、非法字符和31字长度限制。学号、姓名等字段为纯文本单元格（含以 `=` 开头的内容也不执行公式）；得分率使用百分比数值格式，得分为原0–100数值，空值留空。请求仍受对应访问边界、Origin与 `X-Requested-With: StudentScore` 检查，导出不写数据库。

### 5.1 Excel 样式与导出统计说明（新增）

`GET /api/health` 返回 `score_export_workbook_version: 6`，成功导出响应包含 `X-Export-Workbook-Version: 6` 和原有 `X-Export-Students`。前端下载前检查格式版本，旧后端缺字段时要求重启，不把旧文件描述为有新统计说明。原查询/勾选/筛选参数及名单范围不变。

`server/xlsx_styles.py` 提供所有填充和空值单元格的水平/垂直居中样式，中文微软雅黑、数字Times New Roman。纯数字文本学号以及混合中文字符串中的数字通过 inlineStr 富文本逐段指定字体，不转换为数值，不丢前导零，也不会执行源文字公式。数字型分值、人数及比例使用Times New Roman；说明表比例使用真实数值加 `0.00%` 显示格式。查看设备缺字体时可自行替代，文件不内嵌/下载字体。`table_export.py` 生成的异常Excel共享字体和对齐，但没有正式学生统计说明；CSV格式不支持这些样式及工作表。

`score_export.py` 将实际输出的各班级人员和同一份最终 rows 快照统计为最后的「导出说明」表，班级sheet数量 + 1 才是Excel总sheet数量，说明表不是学生明细。首行10个表头：统计范围、学院、班级、导出人数、参与人数、未参与人数、参与率、合格人数（≥60）、合格率（占参与人数）、合格占导出人数比例。第二行为本次导出合计，之后每个实际班级一行；下面附完整定义和分母说明。未匹配/已剔除人员和其他范围人员不会进入统计。

`export_statistics(rows)`：total 为导出人员数；参与为0<有效数值得分率≤100；不参与为total−participated；合格为60≤有效得分率≤100。空值、0、非数值、非法数值不参与；不因学生状态另行排除。participation_rate=participated/total；qualification_rate=qualified/participated；qualified_total_rate=qualified/total。参与人数为0时qualification_rate为None，说明表显示文字“—”；其余两个比例在非空导出中正常为0。空导出仍拒绝，不生成假统计表。

全校/学院/班级并集、指定学生去重、全选筛选快照+取消勾选及不合格筛选都在原SQL中确定最终人员集，统计只取该集合。总计不是各班比例平均值，不根据前端当前页或另发数据库查询猜测人数。人数整数、比例原值为数值，百分比仅显示到两位小数，不修改原分值。

合格规则由 `grade_rules.py` 与前端 `grade_values.js` 统一：有效数值≥60（含60、不按进度排除）。总览、成绩明细、Excel合格人数一致；不合格仅为无有效数值或<60。考试状态未开始/进行中另列进度跟进，≥60不能被导出为低分不合格。结算人数及平均值仍保持原结算规则，真实完成0参与平均；Excel参与率继续按>0计算，不推断实际缺考。导出保持只读，不新增数据库迁移或运行依赖。

### 5.2 名单剔除（新增）

`GET /api/health` 返回 `roster_removal_api_version: 1`。前端无该字段时阻止剔除，要求重启后端。新增表通过 `CREATE TABLE IF NOT EXISTS` 在启动时创建，不改写现有学生分值。schema_version 继续为2；**不能用不认识剔除标记的旧程序继续操作该库**。

| 方法/路径 | 用途 |
|---|---|
| POST `/api/imports/analyze` | 复用 XLS/XLSX 上传；上传不删除数据 |
| GET `/api/imports/{upload_id}/sheet` | 复用表头重新识别；指定工作表及表头行 |
| POST `/api/removals/preview` | `{upload_id, sheet_index, header_row}`；固定 A 列，不接受列映射/姓名条件 |
| GET `/api/removals/{preview_id}/preview.csv` | 服务端全量学号、匹配结果及所有错误；不受页面前25条限制 |
| POST `/api/removals/commit` | `{preview_id, confirm:true, confirmation:"确认剔除", expected_count:预览matched人数}` |
| GET `/api/removals?page=1&page_size=10` | 全部持久批次分页，最多50条/页，返回 items/total/page/pages |

仅处理选中工作表、指定表头之后的数据；A列表头须识别为学号。学号调用名单的严格 `identifier(Cell)` 校验，保留文本前导零和大小写，不用姓名、院系或班级纠正/推测学号。重复合法学号去重；空行及重复表头跳过；空学号但其他列有值、公式、错误值、布尔值、科学计数法文字、非整数数字、超过15位的数字学号等阻止整批剔除。无有效学号也不能提交。

预览只读数据库一致快照；暂存不可变 payload（60分钟），summary 包含 source_rows/unique_ids/matched/unmatched/duplicate_rows/error_count/score_rows/change_rows/already_excluded，sample 为前25个去重学号的数据库身份和当前成绩，errors 为前100个错误，完整CSV不截断。未匹配者只登记剔除学号，不删除任何同名人员。

提交检查明确确认、匹配人数、预览错误、revision。`BEGIN IMMEDIATE` 内写批次汇总、`excluded_students` 标记、受影响成绩批次关联，然后按相同学号集合删除 `score_import_rows`、`student_changes` 和 `students`，只清理本次删除导致空出的目录、递增revision。任何SQL失败整批回滚；并发重复提交和重启后相同令牌重试幂等返回同一批次，确认人数不一致拒绝。没有在线撤销或解除剔除接口。

`excluded_students` 仅存 student_id/removal_batch_id，不保存姓名或分值；全部有效学号（含未匹配）登记剔除。普通名单预览新增 action=excluded 和 summary.excluded，提交再次核对剔除表，不重建学生，计入历史 skipped_count。成绩导入仍不创建名单外人员；后来主动导入新成绩文件可产生新的历史异常，但不进正式统计。删除后关联成绩批次撤销返回409，避免删除部分明细后意外执行不完整的整批撤销。

名单/成绩历史批次的文件摘要、原始汇总仍保留，不代表当前人数；被剔除的逐人快照不留在正式业务表或剔除审计中。正式学生查询、人数、目录、名单/成绩导出以及既有异常汇总投影自动反映删除结果。旧备份和源Excel不改动，删除不等于安全擦盘。提交后本次上传及过时预览尽力清理，清理失败不把已成功数据库事务误报为未删除；其他窗口剔除后新生成的预览不主动清掉。

### 5.3 辅导员分类待跟进清单（新增）

健康能力 `follow_up_api_version=4`（新增答题时间/时长字段）。正式分类从当前students读取，历史异常复用 `score_issues.issue_snapshot`；一条读事务核对revision，名单、分类计数及页面来自一致快照。不修改学生、分数、状态或异常审计，也不增加联系/催办操作记录。

| 接口 | 参数及说明 |
|---|---|
| GET `/api/follow-up` | kind、q（最多120字）、college_id/class_id（实际ID）、page、page_size（1–100）；返回items/total/page/pages/counts/scope_students/revision及请求范围 |
| GET `/api/follow-up/export.xlsx` | kind/q/college_id/class_id，可选expected_revision；完整范围全部页，一张带分类原因的核对清单，不替代正式分班成绩工作簿 |
| GET `/api/follow-up/export.csv` | 同范围及revision核对，UTF-8 BOM、文本公式转义 |

kind取值：pending（所有待跟进）、missing（空/无有效数值）、not_started、in_progress、completed_low（已完成/已录入且有效数值<60，含真实0）、other_low（其余状态有效数值<60）、at_60（恰好60，已合格边界核对）、issues（历史异常）。正式pending按人员去重，包括无有效数值、数值<60或进度未开始/进行中；分类可交叉，at_60不因分数单独计为pending。≥60且未完成可同时是合格和待进度跟进。旧at_90跟进分类已移除，旧客户端需刷新，传入旧分类返回422，不会冒充60分分类。

counts是当前范围及q下各分类记录数，不限当前页；scope_students只计当前正式名单匹配q的人员。issues按合法学号汇总，非法/缺失ID独立成项，排除已剔除学号；历史异常不代表尚未解决，也不计入正式pending。在学院/班级范围只允许绑定实际在册学号的异常，不根据源学院/班级文字猜测未知归属；无法绑定者仅全校显示。历史分值与当前成绩分别返回，名单外人员qualified为None，不伪造正式资格。

导出忽略分页，返回 `X-Export-Records`、`X-Follow-Up-Api-Version: 4` 和 `X-Data-Revision`；前端总是传expected_revision，读取后数据改变返回409，要求刷新；无记录返回400，错误/旧后端不伪造0或下载旧文件。Excel沿用微软雅黑/数字Times及空单元格居中；CSV仅纯文本。所有范围ID为绑定参数，q按字面搜索，不作为SQL/公式执行。用户浏览清单时的成绩页状态/得分下拉不传入该接口，清单有独立分类和搜索。

### 5.4 审查后的校验与快照保障

新增 `read_snapshot_api_version=1`；GET `/api/hierarchy`、`/api/students`返回读取快照的revision。总览多个聚合SELECT、分页count/rows同一只读事务，不混用并发修改前后的结果。前端基础目录与统计版本不一致会重读，列表缓存绑定自身revision，过期请求丢弃。POST `/api/students/export.xlsx`新增可选expected_revision及响应X-Data-Revision；新页面传读取版本，冲突409，不传时仍按最新一致范围导出。

JSON操作确认/范围开关严格为布尔值，索引/ID/版本严格为整数，不接受将true当作1、将字符串yes当作确认的隐式转换；URL中的数字查询参数仍按常规解析。学号仍是文本且不补齐。新确认输入统一Decimal ROUND_HALF_UP两位小数，导出不重新舍入原已存分值。公式、错误、NaN/Infinity不静默视为缺失。

`staging.py`统一临时对象/时间/必要字段校验与源二进制SHA-256校验。提交成功后的删除权限错误/其他损坏预览不改成保存失败，清理按既有过期机制重试。`request_limits.py`限制实际请求体字节，不依赖Content-Length；分块超限413，非法长度400。

数据库保持schema_version=2，启动按原子事务安装附加剔除INSERT/学号UPDATE触发器，旧程序尝试重建已剔除人员会被数据库拒绝；应用仍在预览和提交时先跳过剔除人员。管理员直接删除标记/恢复备份不属于普通导入保护。确认剔除还复核当前身份和关联记录数量，外部编辑未更新revision也不会按变化后的预览盲删。

导出大量选择/取消/目录ID通过TEMP表参数化逐条插入，避免SQLite参数上限；历史异常先在SQL按实际学号归属及剔除标记筛选，跟进正式项只读必需字段。Excel按班级逐页压缩，行数或已存数值异常会报可读错误，而非生成损坏文件。审查结果与保留边界见 [CODE_REVIEW_REPORT.md](CODE_REVIEW_REPORT.md)。

### 5.5 答题时间及兼容升级

`GET /api/health`新增`exam_time_api_version=1`。已有schema_version=2库在启动事务中通过ALTER TABLE添加三个可空列，保留成绩、版本和历史审计；附加字段升级时递增revision使旧暂存预览失效。V1迁移仍先备份再事务升级；重复启动幂等，不读取或自动导入任何原始报表。

开始/结束映射分别识别“开始时间”“结束时间”等表头（标准报表索引11、12，对应L、M）；不映射系统评定时间。`exam_time.clean_exam_time`校验完整日期时间或带明确日期格式的Excel日期序列，读取工作簿1900/1904日期系统。`elapsed_seconds`对结束减开始取整秒，跨日不循环；负时长、非法时间/公式和时区缺失不一致列入invalid。缺一端或“未正常交卷”返回空时长，原标记在raw_started_at/raw_finished_at及notes中保留，状态/成绩不被推断改写。

学生列表/详情及跟进正式项返回exam_started_at、exam_finished_at、answer_duration_seconds；历史导入行增加同名清洗字段及raw_started_at/raw_finished_at，before/after包含时间快照。CSV/Excel显示HH:MM:SS，空值留空、真实0显示00:00:00，小时数可超过24；分班表在原七列后追加开始时间、结束时间、答题时长，筛选区间扩为A:J，导出说明统计列保持不变。清洗/批次导出追加本次/导入前/合并后时长，不把保护或异常行描述为本次已覆盖。异常人员汇总的best_exam_started_at、best_exam_finished_at、best_answer_duration_seconds绑定原最高分来源，出现明细保留各行时间，绝不拼接不同尝试。

相同成绩/状态且原时长缺失可补齐完整时间；同分已有时间默认不改来源，显式replace可更正。同分状态推进且新文件无完整时间时保留旧时间；较低/空值成绩受保护时不补低分时间。新高分无时间则清空旧分时间，避免错配。同批并列最高分的不同完整时间列为重复冲突。提交和整批撤销与分数一并写入/还原时间；旧审计无这些字段时兼容为空。名单更新和手工状态/分数修正保留时间，编辑界面只读展示，不提供猜测或修改源时间的入口。数据总览不新增时长统计。

时间列/值完全缺失不影响导入、查看或导出；“暂无/暂无记录/未提供/未填写/未录入/未结算”和隐藏空白按可选缺失处理。旧JSON完全没有时间键也兼容为“—”；旧审计继续可导出/撤销，无需先重新导入。

前端导入按analyze.fields识别时间能力；旧服务不支持或缺少fields元数据时，正常展示成绩映射，禁用可选时间控件、过滤时间映射，并提供普通兼容说明，不因缺时间功能阻止原成绩。换工作表/重识别表头及提交前都重新过滤，避免把旧服务不认识的字段发回去。有时间的新文件在旧服务上仍只导入成绩，页面明确说明本次不保存时间；要补时长再升级服务并提供报表。

成绩工作簿v6和跟进v4包含时间；分班下载同时接受Workbook-Version=5、跟进接受Api-Version=3，正常下载旧格式并明确普通提示“不含答题时间”，不伪称有新列。缺版本或更旧的及格规则/接口仍保留原保护，以免导出错误的60分名单。仅想补齐旧记录时长才需重新上传含时间报表并确认；不能从未保存过时间的旧审计凭空恢复。

### 5.6 异常定位、原始整行与当前人员查询

能力：health 新增 `score_trace_api_version=1`、`score_people_api_version=1`，原 schema_version=2 不变，无新表或历史回填。新 analyze.fields 提供可选 `yiban_id`；前端按此能力启用映射，旧服务不发送不认识的字段，原成绩导入仍可继续。新增接口缺失只显示该组件的可读提示，不阻塞旧页面。

`yiban_id` 为可靠清洗后的数字**文本**，`raw_yiban_id` 保存读取原值；不使用浮点转换、不丢前导零、不恢复已丢精度、科学计数法或任意字符中的数字。非法可选易班 ID 只记 notes，不改变成绩行处理类别；精确学号仍是唯一身份关系，账号不写入 students。

新 `record_json.source_row` 包含 version=1、sheet_name/header_row/row_number/row_columns（CSV列数），columns 按原列顺序保存 index（0起始）、column（A/B/…）、label、value、text、number_format、formula、error。重复/空表头不折叠，CSV单元格的原始文本空白/换行保留；公式不执行，仅保留读取缓存值与标记，非有限值转明确文本。不留原文件二进制，不恢复Excel排版或公式表达式。未处理的空行/重复表头仍按原规则跳过。全部列快照属于本地持久审计，随原行一起受名单剔除清理；会增加存储与备份体积。旧预览/旧记录无这些键时继续按旧字段处理，不能凭空恢复未保存过的列。

异常 summary 追加 yiban_ids/raw_yiban_ids；occurrences 追加 yiban_id/raw_yiban_id/notes/sheet_name/source_row_available，**不在每页重复返回所有源列**，点击展开再读取单行。q 增加原始/清洗后账号、批次名、文件名匹配，并兼容可可靠清洗的粘贴ID；不改变原按学号分组和最高分来源。汇总/出现导出在原列后追加账号等信息，不移动原有列，原始全部列通过单行查看，不塞进一个可能超出Excel单元格上限的JSON列。

`/api/scores/people` 在一个 read_snapshot 中读取当前正式 students、成绩来源、revision，以及有精确在册学号的历史 yiban_id 检索提示。q 为字面包含匹配（大小写/全角归一化），支持学号、姓名、学院班级、专业及历史易班ID；从不因报表同名或账号同号合并身份。返回全部当前学生字段并追加 historical_yiban_ids、qualified（统一≥60）、settled（有效数值且非未开始/进行中）；分页有 total/pages，越界页收敛。名单外/已剔除人员不变成在册查询结果，未知状态筛选400、参数越界422。

前端在异常“出现记录”和批次明细提供全宽原始行展开区域；当前人员组件在“异常人员”旁，和历史异常可互相带学号定位，关闭恢复到可见入口。七种登记状态、合格/结算/占位0、异常类型和批次处理含义统一说明；显示的是本地已存状态，绝非平台在线状态或自动异常处置认证。移动端人员状态改为纵向卡片，原异常表保留横向滚动，长内容只在弹窗内部滚动。筛选/刷新保留已有结果与输入，失败可重试，过期响应丢弃。

## 6. 扩展多考试时不要复用批次代替考试

建议未来新增 `exams(id,name,date,total_score,...)` 和 `student_exam_scores(exam_id,student_id,status,raw_score,score_rate,version,...)`，唯一约束 `(exam_id,student_id)`；成绩批次关联 exam_id。这是未来设计建议，当前包未实现这些表和页面。

## 7. 参考实现依据

- Python CSV 官方文档：https://docs.python.org/3/library/csv.html
- Python sqlite3 官方文档：https://docs.python.org/3/library/sqlite3.html
- SQLite ALTER TABLE 与通用迁移流程：https://www.sqlite.org/lang_altertable.html
- FastAPI 文件上传：https://fastapi.tiangolo.com/tutorial/request-files/

这些是库机制参考；本项目清洗分类、业务语义、零分策略与测试结果来自本项目代码和附件验证，并非官方库对学校成绩的定义。
