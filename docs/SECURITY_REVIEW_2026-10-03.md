# 项目安全与逻辑审查

审查日期：2026-10-03（北京时间）。本次为审查，未修复业务源码、升级依赖或访问真实供应商；所有复现使用内存数据库、模拟检索/判断和小型合成文件。

遍历了 backend、frontend、contracts、docs、evaluation 和启动脚本，重点追踪 HTTP 写入、文件导入、任务状态、原文定位、证据来源、评分和外部调用。发现 8 项应用层问题及 1 组依赖风险。P1 表示应优先处理，P2 表示应在后续迭代处理；其中鉴权问题是项目已公开说明的部署边界，依赖风险有特定启用条件。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| 后端既有测试 | 444 通过；2 条依赖弃用警告 |
| 前端既有测试 | 74 通过 |
| TypeScript 类型检查 | 通过 |
| 前端生产构建 | 通过，Vite 6.4.3 |
| npm audit | 6 个受影响包条目：1 critical、2 high、3 moderate；条目含间接依赖传播，不代表 6 个独立漏洞 |
| 隔离复现 | 见项目内 backend/.pytest-runtime/security_review_probe.py |

既有测试通过不覆盖下述遗漏。未进行真实联网准确率评测、浏览器实际攻击、生产压力测试或后端依赖的完整在线 CVE 扫描。

## 发现概览

| 编号 | 优先级 | 问题 | 主要定位 |
| --- | --- | --- | --- |
| 1 | P1 | 未确认的用户出处能制造独立来源加分 | backend/app/knowledge_retrieval.py:25、75 |
| 2 | P1 | 部分搜索失败被吞掉，任务仍成功并给总分 | backend/app/retrieve.py:495 |
| 3 | P1，联网共享时 | 知识库可匿名枚举、读取和删除 | backend/app/api/knowledge.py:20、33、41 |
| 4 | P2 | 普通事实因“值得”“推荐”等词被排除核验 | backend/app/extract.py:26 |
| 5 | P2 | 请求体大小保护遗漏人工复核接口 | backend/app/api/request_limits.py:19 |
| 6 | P2，条件性 | 无请求体的重建索引缺少来源/CSRF 校验 | backend/app/api/knowledge.py:57 |
| 7 | P2 | 单条支持指数未保存，列表、详情和导出为 null | backend/app/pipeline.py:181；backend/app/scoring.py:44 |
| 8 | P2 | 总预算耗尽后的声明无法直接重试 | backend/app/storage.py:120；frontend/src/components/ClaimCard.vue:97 |
| 9 | P2，开发工具条件性 | 旧版 Vitest 及间接依赖存在已知漏洞 | frontend/package-lock.json:2191、2697、1168 |

## 1. 未确认的出处被当作独立证据来源

`merge_evidence()` 依赖 URL 站点、内容 hash 和片段来合并来源。知识库上传的 `source_url` 由用户自由填写，后端没有验证该网页是否存在、是否来自该机构、是否包含上传正文，但不同站点会成为不同证据簇。每条知识库证据的 authority 为 0.55；判断返回直接支持后 relevance=0.9、time_fit=0.5，单源质量为 0.645，两源组合为 0.774，超过 credible 的 0.75 门槛。

复现：在同一内存知识库上传两份“甲公司成立于2001年”的资料，分别填写 example.gov 和 example.edu 的地址，正文附加不同说明以产生不同 hash。没有网络抓取，模拟判断仅返回与上传正文一致的支持关系。结果为 2 个独立证据簇、label=credible、任务支持指数=89。

影响：用户或可访问上传接口的人可以用自行编写的资料制造交叉印证；界面上的“出处尚未确认”提示没有约束实际判定。本次证明的是来源信任与评分路径，不代表已测试真实 MiMo 对任意恶意资料的响应。

建议：显式保存出处验证状态、导入者及上游来源身份。未验证的上传资料不要仅凭自填 URL 获得来源独立性加分，可先归入共同的未验证来源，或限定为参考材料，待原始出处确认后再参与充分证据裁决。

## 2. 部分搜索失败不进入失败记录

`SearchBackedRetriever.retrieve()` 累计 `provider_errors`，只有所有查询都失败时抛异常。某些查询成功、其余查询失败时，错误没有进入 `claim.retrieval_warnings`，流水线也无法据此记录失败来源。若已有候选，后续鉴权、额度等错误同样可能只导致停止查询而不公开失败。

复现：3 次检索中第 1 次返回相关政府网站正文，后 2 次抛出 SEARCH_TIMEOUT；模拟判断返回对该正文的直接支持。任务最终 status=succeeded、failed_providers=[]、warnings=[]、总分=91。

影响：系统没有完成计划中的反证搜索，却展示了没有技术失败的成功结果和总体指数，违背 docs/ARCHITECTURE.md 中“技术性部分失败时隐藏总体支持指数”的约定。

建议：保留已取得证据，同时公开每次查询的清洗错误码；任一计划查询技术失败就设置 retrieval_warnings 和任务 partial，使总体评分遵守现有失败门槛。

## 3. 知识库没有身份认证或归属隔离

所有知识库路由仅依赖 `PipelineDependency`，没有身份、权限或资料归属检查。匿名 GET /api/knowledge/documents 能取得完整资料列表与 ID，再 GET 详情能读取正文，DELETE 能删除资料。任务原文、结果和人工修改接口同样缺少所有权控制，但任务 UUID 使随机枚举更难；知识库列表没有这一门槛。

复现：对独立内存资料，无 token 的列表、详情和删除分别返回 200、200、204。

这是 README 已说明的无登录 MVP 边界，不是新引入的回归。按启动脚本仅监听 127.0.0.1 时风险主要限于本机可访问者；如果在局域网、公网或多用户环境提供接口，则涉及资料泄漏、删改和污染，必须优先处理。

建议：共享部署前加入身份认证与任务/知识库归属校验；仅供本机使用也可采用启动时生成的访问令牌或在代理层限制访问。CORS 不能限制非浏览器客户端。

## 4. 事实被关键词误判成观点

`_kind()` 用不带语义边界的正则检查“观点|建议|我认为|值得|…|推荐”，一旦匹配就直接返回 opinion/not_applicable，且该分支先于统计、论文识别。

已复现以下两句均被标记为 opinion/not_applicable：

- 值得注意的是，甲公司成立于2001年。
- 该大学2025年推荐免试录取人数为1000人。

影响：真实可核验内容不再执行检索，并从可核验声明分母中排除；添加无关引导语就可能规避核验，造成覆盖率偏高。

建议：只将明确的主观评价或建议本身排除，保留其中的事实从句；“推荐免试”等实体/制度名不得触发意见分类。补充事实和意见混合句的回归样本。

## 5. 人工复核请求没有解析前的大小上限

请求体中间件只处理 POST /api/tasks 与 POST /api/knowledge/*，遗漏 PATCH /api/claims/{id}、声明补充、拆分、合并、撤销等接口。字段 max_length 是 JSON 全部接收并解析之后的校验，不能替代接收阶段的内存保护。

复现：约 300 KB 的创建任务请求被中间件以 413 拒绝；同等大小的 PATCH 声明和 POST split 返回的是字段校验后的 422，而非 413，即大请求已经到达解析/模型校验路径。

影响：这些接口可以接收并解析远大于业务允许值的 JSON；并发大请求可消耗内存。没有发送大规模攻击负载或尝试使服务崩溃。

建议：对所有有请求体的写方法实行接收字节上限，仅上传文档使用单独的较大阈值；同时保留 Content-Length 与流式累计的双重保护，并在反向代理配置相应上限。

## 6. 无请求体的重建索引接受不可信 Origin

POST /api/knowledge/reindex 无请求体校验，也没有检查 Origin 或 CSRF/访问令牌。CORS 不匹配时仍执行实际请求，只是不返回允许跨域读取的响应头。

复现：携带 Origin=https://untrusted.example、没有请求体与 Content-Type 的 POST 进入重建索引 handler 并返回 200，Access-Control-Allow-Origin 缺失。为避免真实模型计算，handler 在此项复现中替换为记录调用的轻量模拟函数。

如果浏览器允许页面访问目标本地/网络服务，这属于可跨站触发的昂贵写操作；真正浏览器能否发出请求，还受本地网络访问权限及浏览器策略影响。本次没有在浏览器复现这一步。重建索引也不在 TaskAdmissionLimit 的限流范围内。

当前安装的 FastAPI 0.141.1 默认严格检查 JSON Content-Type，因此无 Content-Type 的任务创建和文档导入返回 422，未据此报告这些接口的 JSON 跨站写漏洞。[MDN CORS 文档](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CORS)解释了简单请求与响应读取限制的区别。

建议：为所有写操作增加来源校验和访问/CSRF 令牌；把重建、导入、搜索等模型工作纳入并发与频率限制。不要把本地浏览器网络限制当作唯一服务端保护。

## 7. 单条支持指数只在临时对象中计算

`judge_claim()` 设置标签后，流水线先将声明保存到 SQLite；随后 `build_task_summary()` 调用 `summarize()`，才对新读取的 Claim 对象设置 support_score，但没有再保存。列表和详情重新读取数据库，因此仍为 null。导出先读取摘要，再重新查询声明，同样丢失该计算结果。

复现：模拟的充分支持证据产生 credible 和任务总分95；调用 support_score(claim) 得到95，而数据库声明和 JSON 导出的 support_score 均为 null。

影响：声明卡片与导出缺少支持指数，总体指数却正常，风险排序或后续消费该字段也会受到影响。

建议：选择统一策略——在最终保存前计算并持久化，或在所有声明查询序列化前计算；避免只在生成摘要时修改临时对象。补充 API 列表、详情、JSON/Markdown 导出的一致性验证。

## 8. 预算耗尽的未执行声明无法继续核验

流水线把总预算耗尽后尚未处理的声明设为 unchecked，通常没有标签，也没有 manually_edited 标记。`begin_claim_retry()` 仅允许 failed、evidence_insufficient 或“人工编辑且 unchecked”。前端按钮使用相同限制，所以预算跳过项既没有按钮，直接调用后端也得到 RETRY_NOT_ALLOWED。

复现：设置合法的1秒任务预算，第一条模拟检索耗时1.05秒，第二条被标记 unchecked；对此声明申请重试返回 RETRY_NOT_ALLOWED。测试模拟检索不代表真实供应商会越过 deadline，只用于触发已有预算耗尽分支。

影响：用户必须重新提交全文，或先进行无意义的人工编辑，才能核验这些声明；在慢外部服务场景中不便恢复。

建议：保存 unchecked 的原因，允许“预算耗尽、尚未执行且无未解决指代”的声明按正常次数限制重试，保持指代不明项的保守限制，并同步调整前端按钮。

## 9. 开发/测试依赖的已知漏洞

实际安装及锁文件均包含 Vitest 2.1.9、其内部 Vite 5.4.21、brace-expansion 2.1.4。顶层开发/构建使用 Vite 6.4.3，不能将内部旧 Vite 的漏洞直接等同于目前 npm run dev 的漏洞。

npm 官方审计服务返回6个受影响包条目，涉及 Vitest UI/API 服务的文件读取/执行、Vitest mock 路径遍历、内部旧 Vite 的 Windows 文件访问绕过及传递依赖的拒绝服务风险。相同漏洞可能通过依赖链显示在多个包条目中。

最严重的 Vitest 条目是 [GHSA-5xrq-8626-4rwp（维护者公告）](https://github.com/vitest-dev/vitest/security/advisories/GHSA-5xrq-8626-4rwp)，需要启用相关 UI/API/Browser Mode；项目当前测试脚本是 vitest run，没有配置相应服务，故本次未确认可利用的运行时远程代码执行。旧 Vite 的 Windows 路径问题可参考 [GHSA-fx2h-pf6j-xcff（维护者公告）](https://github.com/vitejs/vite/security/advisories/GHSA-fx2h-pf6j-xcff)。

建议：升级 Vitest 及其内部 Vite/mock 依赖、更新 brace-expansion 的安全版本，检查 Node 和 Vite 的兼容性，再跑前端测试、类型检查、构建与 audit。不要直接执行未经评估的 audit fix --force；本次未修改依赖。

## 额外资源保护观察

PDF 导入上限针对压缩文件字节、页数和提取文字，不能直接约束解析时的总内存/CPU。本次4769字节的合成 PDF 含约4 MiB的压缩内容流，提取结果仅“OK”，导入解析接受，tracemalloc 记录峰值约9.9 MB。当前 pypdf 6.19.0 自带默认约75 MB的单流解压保护，因此不应将本次结果描述为已证实的无限解压漏洞；应用仍缺少跨流/页累计资源预算、解析隔离和硬超时。

此外，import_document() 在重复正文和容量检查前先解析并生成向量，重复或超容量请求仍能消耗计算；_encode() 的 deadline 约束获取锁的等待，没有中止已经开始的同步模型计算。建议将廉价准入检查提前，限制知识库并发，并把不可信文件解析置于有资源/时间限制的独立进程。此部分没有进行服务拒绝实验。

## 复现方式与范围

在 backend 目录运行：

```powershell
.\.venv\Scripts\python.exe .pytest-runtime/security_review_probe.py
```

脚本输出各项 JSON 结果，使用内存 SQLite，不调用真实搜索/模型，也不读写用户任务数据库。它位于已有忽略目录，作为本次诊断材料，不属于正式回归测试。

本次未确认业务接口中的 SQL 注入、证据 HTML 执行或任意 URL SSRF：当前数据库查询使用参数绑定，前端未发现 v-html 展示证据，实时检索使用固定供应商端点与供应商返回文本。这是所审查路径的结果，不构成“全项目没有其他漏洞”的保证。Git 跟踪检查未列出 .env 或 backend/data 文件。

推荐优先处理来源信任、部分检索失败记录和事实误分类；对外共享前必须先补鉴权。随后统一请求/计算资源保护、评分一致性与预算恢复，并更新开发依赖。
