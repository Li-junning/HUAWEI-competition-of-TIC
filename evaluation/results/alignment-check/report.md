# 评估报告

- 样本来源：E:\AI 个人项目\项目：华为比赛\evaluation\cases.json
- 运行模式：离线合成证据夹具
- 说明：夹具使用合成证据，仅验证离线语义路径和指标流程；不能代表真实联网搜索准确率。
- 分类分母：6 条含四类可核验 gold 标签的样本
- 人工标注样本：0 条；公开事实演练样本：0 条；合成样本：8 条
- 评估集标签一致率：6/6 (100.0%)
- 真实 AI 回答盲测准确率：0/0 (N/A)
- 人工盲测准确率只适用于本次人工标注样本，不能外推为全部用户查询的总体准确率。
- 弃判率：3/6 (50.0%)；预测为 evidence_insufficient 或 unknown 计为弃判
- 证据不足误判为错误：0 条
- 功能约束状态：measured_on_synthetic_fixture

## 混淆矩阵（行是真值，列是预测；unknown 包括弃判和无效输出）

| gold \ prediction | credible | disputed | incorrect | evidence_insufficient | unknown |
|---|---|---|---|---|---|
| credible | 1 | 0 | 0 | 0 | 0 |
| disputed | 0 | 1 | 0 | 0 | 0 |
| incorrect | 0 | 0 | 1 | 0 | 0 |
| evidence_insufficient | 0 | 0 | 0 | 3 | 0 |

## 功能约束

检查样本分母：7
- claim_count: 通过 3，失败 0
- normalized_claim_contains: 通过 1，失败 0
- evidence_insufficient_not_error: 通过 2，失败 0
- opinion_label_respected: 通过 1，失败 0
- technical_failure_unlabeled: 通过 1，失败 0

## 耗时

总计 37.068 ms；平均 4.633 ms。
