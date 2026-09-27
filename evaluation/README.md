# 离线质量评估

`smoke_cases.json` 保留为早期产品语义冒烟清单。可执行评估使用 `cases.json`，通过后端真实的 `extract_claims` 与 `judge_claim` 处理样本。`cases.json` 中的证据均为人工编写的合成夹具；命中数只能说明离线语义路径和指标计算有效，不能代表联网搜索准确率。

在仓库根目录运行：

```powershell
python evaluation/evaluate.py
```

脚本会在 `evaluation/results/` 生成 JSON 和 Markdown 报告，列出分类分母、混淆矩阵、弃判率、证据不足误判为错误数、功能约束检查和耗时。夹具出现分类错判或功能约束失败时返回退出码 1。

## 人工盲测

仓库目前没有经人工标注的真实 AI 回答样本，所以真实场景准确率为**未测量**。不要把合成样本或公开事实演练的命中数表述为真实准确率。

盲测把系统输入与人工标签分开保存。`blind_cases.json` 只放输入，锁定后才创建单独的 `blind_gold.json`。每个样本应是一条待判定声明；如需保留完整 AI 回答，在 `input_text` 放回答原文，并用 `target_claim` 指定要统计的单条声明。重复声明无法自动区分时，在预测文件中明确填写 `case_id`。

输入样本格式：

```json
[
  {
    "case_id": "blind-001",
    "source": "human",
    "input_text": "世界卫生组织宪章于1948年4月7日生效。",
    "target_claim": "世界卫生组织宪章于1948年4月7日生效。",
    "origin": {
      "provider": "模型或来源平台",
      "captured_at": "2026-09-27",
      "reference": "来源链接、会话编号或经脱敏的出处说明"
    }
  }
]
```

gold 文件单独保存。事实标签要有可打开的来源 URL 和短摘录；可信需有支持来源，错误需有反证，争议需同时有支持和反对来源。证据不足要记录实际检索过的查询词。记录标注人、独立复核人、核验日期和标签理由。无法确认的样本可以标为 `evidence_insufficient`，但未检索不能标成证据不足。

```json
[
  {
    "case_id": "blind-001",
    "expected": {"label": "credible"},
    "annotation": {
      "checked_at": "2026-09-27",
      "annotator": "reviewer-a",
      "reviewer": "reviewer-b",
      "label_basis": "官方页面记载了相同生效日期。",
      "sources": [
        {"url": "https://www.who.int/about/governance/constitution", "relation": "supports", "excerpt": "宪章于1948年4月7日生效。"}
      ]
    }
  }
]
```

先冻结输入样本并运行待评系统，保存原始输出；之后再把人工 gold 文件交给评测脚本。gold 与输入 ID 必须一一对应。脚本会校验 ID、标签、样本来源和证据记录。

## 导入系统预测

在每条 AI 回答对应的系统结果页下载 JSON 报告，把所有任务报告放进同一个目录，再把目录作为 `--predictions` 输入。评测器合并报告中的 `claims`，按 `normalized_claim` 或 `source_text` 与样本 `target_claim` 做唯一匹配。预测重复、匹配到多个样本或 ID 缺失都会报错。匹配不上或有歧义时，使用含显式 `case_id` 的预测 JSON：

```json
[
  {"case_id": "blind-001", "label": "credible"},
  {"case_id": "blind-002", "label": "evidence_insufficient"}
]
```

预测锁定后解封 gold 并运行：

```powershell
python evaluation/evaluate.py --cases evaluation/blind_cases.json --predictions evaluation/system-exports --gold evaluation/blind_gold.json --run-info evaluation/run-info.json --out-dir evaluation/results/blind
```

可附加 `run-info.json` 记录待评版本、联网状态和搜索来源，供报告复现：

```json
{"system":"commit 或发布版本","network_enabled":true,"search_sources":["Tavily"]}
```

在上面的命令末尾加 `--run-info evaluation/run-info.json`。这些信息由运行者记录，报告不会声称独立验证了联网或系统版本。

gold 标签须为 `credible`、`disputed`、`incorrect`、`evidence_insufficient` 或 `not_applicable`；系统预测须覆盖全部样本。预测中的 null 或未知标签按 unknown 统计，并记录无效输出数。导入预测模式不伪造耗时，功能约束标成未测。报告区分人工样本、公开事实演练和合成夹具。公开事实演练不是 AI 回答，不计真实盲测成绩。

`public_fact_seed.json` 提供由公开官方页面支持的事实例句，用来演练标注格式；它不是 AI 回答样本，也没有声称经过独立人工复核。

正式答辩请报告盲测样本来源、系统运行模式（是否联网及搜索来源）、类别分布、混淆矩阵分母、弃判率和证据不足误判为错误数。准确率只描述该批人工标注样本，不能外推为所有用户查询的总体准确率。
