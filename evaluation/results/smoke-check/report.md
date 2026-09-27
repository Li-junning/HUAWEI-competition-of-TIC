# 离线评估报告

- 样本来源：..\evaluation\smoke_cases.json
- 运行模式：离线合成证据夹具
- 说明：夹具与离线预测评估仅验证语义处理和指标流程；不得解释为真实联网准确率。
- 分类分母：3 条含四类可核验 gold 标签的样本
- 准确率：1/3 (33.3%)
- 弃判率：3/3 (100.0%)；预测为 evidence_insufficient 或 unknown 计为弃判
- 证据不足误判为错误：0 条
- 功能约束状态：measured_on_synthetic_fixture

## 混淆矩阵（行是真值，列是预测；unknown 包括弃判和无效输出）

| gold \ prediction | credible | disputed | incorrect | evidence_insufficient | unknown |
|---|---|---|---|---|---|
| credible | 0 | 0 | 0 | 1 | 0 |
| disputed | 0 | 0 | 0 | 0 | 0 |
| incorrect | 0 | 0 | 0 | 1 | 0 |
| evidence_insufficient | 0 | 0 | 0 | 1 | 0 |

## 功能约束

检查样本分母：0

## 耗时

总计 23.539 ms；平均 2.942 ms。
