# MVP API 契约

API 前缀为 `/api`，JSON 字段使用 `snake_case`。时间戳为带时区的 ISO 8601 UTC 字符串。未知值用 `null`，不得使用空字符串伪装未知。

## 自建知识库

- `GET /api/knowledge/status`：资料/片段/已索引向量数量、`keyword|hybrid` 模式和模型配置。状态不代表检索或核验准确率。
- `GET /api/knowledge/documents?offset=0&limit=30`：资料元数据分页，limit 最多 200。
- `POST /api/knowledge/documents`：导入 `{title,publisher?,source_url?,published_at?,tags?,content}` 或 `{title,...,filename,file_base64}`，成功返回 201 和资料元数据。文件支持 `.docx`、`.pdf`、`.txt`、`.md`；旧版 `.doc` 返回 `400 KB_WORD_LEGACY` 并提示另存为 `.docx`。输入不能同时含正文和文件；JSON 请求体上限 4 MiB、文件上限 2 MiB、正文上限 100,000 字符。Word 解压后最多 32 MiB，正文 XML 最多 8 MiB。损坏的 Word 返回 `400 KB_WORD_INVALID`，解析依赖未安装返回 `503 KB_WORD_UNAVAILABLE`，超出资源限制返回 `413 KB_WORD_LIMIT`。发布日期采用 `YYYY-MM-DD`。同一正文返回 `409 KB_DUPLICATE`。
- `GET /api/knowledge/documents/{document_id}`：返回 `{document,pages}`；pages 是原始提取正文的逐页字符串数组，文本和 Word 导入为单份连续文本。Word 按文档顺序提取正文与表格文字，不推断视觉页码，检索片段 `page=null`。
- `DELETE /api/knowledge/documents/{document_id}`：删除资料与检索索引，返回 204；历史任务证据快照不删除。
- `POST /api/knowledge/search`：`{query,tag?,limit?}`，query 最多 2,000 字符、limit 默认 5 且最多 20。返回 `{items,status,score_note}`；每个命中包含原文片段、页码、Unicode 字符位置、版本哈希、`matched_by` 和只用于排序的 `retrieval_score`。
- `POST /api/knowledge/reindex`：每次补建最多 200 个缺少或模型版本不匹配的向量；返回 `{indexed,remaining}`。模型未就绪时返回 503。

证据 `EvidenceItem` 新增 `source_type`（默认 web）、`knowledge_document_id`、`knowledge_chunk_id`、`source_page`、`source_char_start`、`source_char_end`。本地位置是提取后该页的 Unicode 字符区间，不是任务原文的 UTF-16 区间。声明新增 `retrieval_warnings`，网页失败时即使保留本地证据，也需显示警告并保持部分完成语义。历史数据默认字段为空。

来源链接由用户填写，仅做 HTTP(S) 与地址语法安全校验，不代表已认证出处；导入不会抓取任意 URL。详细机制与限制见 `docs/KNOWLEDGE_BASE.md`。

`source_type=knowledge` 的材料保留检索与逐项核对信息，但出处未核实，不进入裁决强度或支持指数。自填站点不计独立来源。网页的计划查询只完成部分时，失败也进入 `retrieval_warnings`；任务为 `partial`，总分为 `null`，已有证据保留。

本地模式仅接受回环客户端。生产模式为私有共享工作区：所有业务 `/api/` 请求要求有效 HttpOnly/Secure/SameSite=Strict 会话 Cookie，未登录返回 `401 AUTH_REQUIRED`。所有写入同时要求 `X-CSRF-Token`，缺失或错误返回 `403 CSRF_REJECTED`；未配置的 Origin 或跨站写入返回 `403 ORIGIN_REJECTED`。所有 API 响应使用 `Cache-Control: no-store`。

- `GET /api/auth/session`：返回 `{required,authenticated,csrf_token}`；未登录时 token 为 null。本地模式 required=false。
- `POST /api/auth/login`：JSON `{password}`，必须带 `X-Verifier-Request: 1`；返回有效会话状态并设置 Cookie。密码错误返回 401，每进程每分钟最多 5 次尝试，超出返回 `429 LOGIN_RATE_LIMIT`；请求最多 8 KiB。
- `POST /api/auth/logout`：要求有效 Cookie 与 CSRF token，立即撤销当前会话并删除 Cookie。会话最长 8 小时，服务进程重启后失效。

所有 POST/PATCH/PUT/DELETE 的 `/api/` 请求体上限为 256 KiB，知识库导入单独为 4 MiB；超过返回 `413 REQUEST_TOO_LARGE`，没有 Content-Length 的流式请求同样受限。创建任务、重试、知识库导入、搜索、重建索引共用每进程最多 3 个并发操作（受配置限制）、每分钟 10 次的准入限制，超出返回 `429 TASK_RATE_LIMIT`。PDF/Word 在独立进程解析，最多同时 2 个、单次 15 秒，超时返回 `413 KB_PARSE_TIMEOUT`。详细资源限制见上线说明。

## 枚举

```text
TaskStatus = created | running | succeeded | partial | failed | interrupted
ClaimState = pending | extracting | retrieving | judging | done | failed | unchecked
ClaimLabel = credible | disputed | incorrect | evidence_insufficient | not_applicable
EvidenceRelation = supports | refutes | partially_supports | irrelevant | unknown
```

## 创建任务

`POST /api/tasks`

```json
{
  "input_text": "待核验文本",
  "claim_limit": 15
}
```

返回 `202`：

```json
{
  "task_id": "t_<uuid>",
  "status": "created"
}
```

`input_text` 去除首尾空白后不得为空，最多 20,000 字符。`claim_limit` 默认为 15，范围 1–15。

## 任务摘要

摘要新增 `segmentation_method`：`rules`（本地规则）、`mimo`（模型语义切分）、`rules_fallback`（模型失败后回退规则）。历史任务默认为 `rules`；切分回退不算证据来源失败。`GET /api/status` 新增 `segment_mode`（`rules` 或 `mimo`），仅表示配置方式。

复合句按独立事实抽取。`source_text` 与 UTF-16 位置只引用该条原文片段；`normalized_claim` 可补回原文中的共同主语、时间和范围，供检索和判断使用。例如原文片段“并收购乙公司”可规范化为“甲公司并收购乙公司”。补回内容不能是外部知识。声明计数及上限按拆分后的数量计算，历史任务不会自动重新抽取。

`GET /api/tasks/{task_id}` 返回：

```json
{
  "task_id": "t_<uuid>",
  "status": "partial",
  "created_at": "2026-09-14T12:00:00Z",
  "updated_at": "2026-09-14T12:00:02Z",
  "input_char_count": 120,
  "claim_limit": 15,
  "claims_extracted": 4,
  "claims_processed": 3,
  "claims_unchecked": 1,
  "truncated": false,
  "failed_providers": ["search"],
  "coverage": {
    "verifiable_claims": 3,
    "processed_verifiable_claims": 2,
    "adjudicated_verifiable_claims": 1,
    "processing_coverage": 0.6667,
    "verification_coverage": 0.3333,
    "extraction_coverage": null
  },
  "label_counts": {
    "credible": 1,
    "disputed": 0,
    "incorrect": 0,
    "evidence_insufficient": 1,
    "not_applicable": 1
  },
  "score": null,
  "score_note": "核验覆盖率不足，未显示总体支持指数",
  "error_code": null
}
```

## 任务原文

`GET /api/tasks/{task_id}/input` 返回 `{task_id,input_text}`，其中 `input_text` 是创建任务时去除首尾空白后保存的完整原文。声明的 UTF-16 半开区间以此文本为准，刷新后可以重新定位；响应带 `Cache-Control: no-store`，不会把原文加入任务摘要的周期轮询。任务不存在返回统一的 404 错误。

此接口沿用当前任务 API 的访问范围：能连接服务并持有任务 ID 的人可以读取任务和原文，没有新增身份或任务所有权隔离。

## 声明列表与详情

`GET /api/tasks/{task_id}/claims?offset=0&limit=20` 返回 `{items,total,offset,limit}`。列表项包含：

```json
{
  "claim_id": "c_<uuid>",
  "task_id": "t_<uuid>",
  "source_text": "原文片段",
  "char_start": 0,
  "char_end": 8,
  "type": "statistic",
  "normalized_claim": "规范化声明",
  "entities": [],
  "conditions": [],
  "queries": [],
  "label": "evidence_insufficient",
  "support_score": null,
  "reason": "当前可访问证据未覆盖相同统计口径",
  "state": "done",
  "retry_count": 0,
  "evidence_cluster_ids": []
}
```

`GET /api/claims/{claim_id}` 在列表项基础上返回 `evidence_clusters` 和可选 `paper_check`。每个证据项至少包含 `evidence_id/url/title/publisher/published_at/retrieved_at/excerpt/relation/quality_reason/is_reprint`，绝不包含网页 HTML。

证据项可附带 `checks`（最多 8 项；旧数据默认为空数组），其中每项包含 `part_id`、`part_text`、`relation`、`excerpt`。这些是服务端完成引用定位和语义约束校验后的逐项结果，模型未通过校验的引用不会保留。单项文本必须与当前声明的核对项一致才可汇总；不同证据的部分支持可共同覆盖整句，但缺失项不能由其他项的来源数量补足。前端证据详情可展开逐项核对，JSON 导出同样包含这些字段。

## 人工复核声明

任务结束后，`PATCH /api/claims/{claim_id}` 可修改 `normalized_claim`（1–2000 字符），请求体也可传 `reviewer`（审核人署名，最多 40 字符）。`source_text`、原文 UTF-16 区间和任务原文不变；为避免旧证据支持修改后的文字，服务会清空标签、分数、查询和证据，并把声明标记为 `unchecked`。返回项会带 `manually_edited: true`。用户可从声明详情重新检索修改后的文字；在该修改尚未重查时允许一次显式重试，重试使用现有任务/provider 调用预算。获得新结论后会遵循常规重试限制。`DELETE /api/claims/{claim_id}?reviewer=姓名` 会从任务列表、摘要计数和后续导出中移除此声明，但不会改写原文或其他声明。

人工调整断句使用以下接口。每次操作都记录在任务的操作历史中；调整涉及的声明会清除旧判断并标记为 `unchecked`，需要再次检索。位置使用 JavaScript UTF-16 半开区间，服务端从已保存原文提取片段，拒绝越界、切断代理对或与现有声明重叠。补充和拆分仍受任务 `claim_limit` 约束。

| 接口 | 请求体主要字段 | 效果 |
| --- | --- | --- |
| `POST /api/tasks/{task_id}/claims` | `char_start`, `char_end`, `normalized_claim`, `reviewer` | 补充遗漏声明 |
| `POST /api/claims/{claim_id}/split` | `split_at`, `first_claim`, `second_claim`, `reviewer` | 在原文指定位置拆成两条 |
| `POST /api/tasks/{task_id}/claims/merge` | `claim_ids`（相邻两条）, `normalized_claim`, `reviewer` | 合并成一条 |
| `GET /api/tasks/{task_id}/review-history` | 无 | 按时间倒序返回操作类型、署名、时间、修改前后文字和撤销状态 |
| `POST /api/tasks/{task_id}/review-history/{event_id}/undo` | `reviewer` | 按相反顺序撤销最近一次尚未撤销的操作 |

撤销会恢复该操作前的声明及判断。若之后发生重新检索等状态变化，为避免覆盖新证据，返回 `409 STATE_CHANGED`。其他可能的冲突包括 `TASK_BUSY`、`INVALID_RANGE`、`CLAIM_LIMIT`、`UNDO_ORDER`。生产模式有工作区访问认证，但审核人署名仍由操作人输入，不代表独立账户身份。

## 重试

`POST /api/claims/{claim_id}/retry` 允许证据不足、技术失败、有 `retrieval_warnings` 的检索未完成声明，以及人工调整或预算耗尽的 `unchecked` 声明。指代不明确的声明需先人工澄清；处理中任务仍拒绝重复重试。默认最多 2 次，受后端重试配置约束；超过限制返回 `409`。返回 `202` 和声明当前状态，接受时即清除旧标签及指数。重试必须计入任务/provider 调用预算。

声明新增 `unchecked_reason: task_budget | unresolved_reference | null`，用于区分可继续执行的预算耗尽与需澄清的指代；历史任务默认 `null`，原有预算耗尽说明仍可识别。列表、详情和导出的单条 `support_score` 采用同一计算规则，旧报告缺失的指数在读取时派生，无需重写数据库。

## 导出

`GET /api/tasks/{task_id}/export?format=json|md`。仅完成或部分完成任务可导出。报告包含运行时间、provider、失败源、截断信息、评分规则版本和免责声明。

## 错误

错误体固定为：

```json
{
  "error": {
    "code": "INPUT_TOO_LARGE",
    "message": "输入文本超过 20000 字符",
    "request_id": "r_<uuid>"
  }
}
```

客户端可见错误不包含堆栈、SQL、文件路径、请求头、密钥或 provider 原始响应。
