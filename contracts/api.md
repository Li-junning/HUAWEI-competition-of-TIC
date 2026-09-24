# MVP API 契约

API 前缀为 `/api`，JSON 字段使用 `snake_case`。时间戳为带时区的 ISO 8601 UTC 字符串。未知值用 `null`，不得使用空字符串伪装未知。

`/api/` 请求无需访问令牌。创建任务请求体上限为 256 KiB，超过返回 `413 REQUEST_TOO_LARGE`。创建与重试共用每进程最多 3 个并发操作、每分钟 10 次的准入限制，超出返回 `429 TASK_RATE_LIMIT`。

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

## 重试

`POST /api/claims/{claim_id}/retry` 只允许对 `evidence_insufficient` 或技术失败声明执行。最多 2 次；超过限制返回 `409`。返回 `202` 和声明当前状态。重试必须计入任务/provider 调用预算。

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
