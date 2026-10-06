# 上线前安全修复记录（2026-10-06）

本次按**私有共享工作区**完成修复。未发布网站、未修改真实数据库、未调用收费搜索/模型。现有用户未提交修改保留，未生成或写入真实访问密码。

## 发现与修复

| 风险 | 原来的行为 | 本次修复 |
| --- | --- | --- |
| 未授权读取/修改 | 可直接读取任务原文、报告、知识库，删除文档、修改声明 | 生产模式所有业务接口要求服务器会话；前端先登录再挂载工作区，退出立即撤销 Cookie |
| 跨站写入 | 只靠 CORS，外站可触发无正文的知识库重建 | 检查 Origin / Sec-Fetch-Site；写入需会话绑定 CSRF header；登录需自定义 header 和 JSON |
| 配置误上线 | 本地匿名接口可能直接暴露，缺少强制生产校验 | 本地仅接受回环地址；专用上线入口只允许 production，密码哈希/域名/HTTPS origin 缺失拒绝启动；关闭 API 文档 |
| 密码与会话 | 无访问认证 | 密码 scrypt N=32768/r=8/p=3 + 随机盐，常量时间比较；Cookie 使用 Secure/HttpOnly/SameSite=Strict；服务器只存随机会话值的哈希；8 小时过期 |
| 修改接口大请求 | PATCH、拆分/合并、人工新增等未受统一大小限制 | 所有 API 写入提前限制字节数，包括分块请求；普通 256 KiB、知识库导入 4 MiB、登录 8 KiB |
| 资源与额度滥用 | 知识库导入/搜索/重建未共享准入限流 | 与创建/重试共用并发与每分钟额度；密码尝试每分钟 5 次；反向代理模板补连接、频率、慢请求限制 |
| 恶意文档消耗资源 | 小压缩 PDF 可以在 Web 进程大量解压、占用解析时间 | PDF/Word 改为独立解析，限制解压流、累计大小、对象/页数；最多 2 个解析进程、15 秒超时；Linux 加内存与 CPU 硬限制 |
| 异常日志泄露 | 未预期异常可把异常文本及堆栈写入日志 | 仅记录异常类型；500 错误不缓存，不回显用户原文、密钥或内部异常 |
| 依赖漏洞 | 前端初次 npm audit：8 个告警（2 critical、3 high、3 moderate）；后端旧 pip 有已知安装漏洞 | Vite 8.3.3、Vitest 5.0.3、Vue 插件 6.0.9、修复的间接依赖；构建工具归入 devDependencies；项目虚拟环境 pip 升级 26.2.1 |
| 静态目录和代理误配 | 缺少可以复用的安全上线模板 | 提供 Nginx / systemd 模板：只发布前端产物、HTTPS、Host 校验、可信代理、CSP、禁止目录索引、低权限运行和资源上限 |

依赖漏洞的触发条件并不相同。Vitest 相关公告主要涉及开发/测试服务器暴露，不能将 npm 告警直接当作本站已经发生远程执行；上线仍必须使用静态构建，不开放开发端口。[Vitest 官方文件读取公告](https://github.com/vitest-dev/vitest/security/advisories/GHSA-82fw-gwwq-j7x9)、[Vitest UI 官方公告](https://github.com/vitest-dev/vitest/security/advisories/GHSA-5xrq-8626-4rwp)。

密码参数参考 [OWASP Password Storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html#scrypt)，会话保护参考 [OWASP Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)，代理信任范围参考 [Uvicorn 设置](https://uvicorn.dev/settings/#http)。

## 验证结果

- 后端全部 **558 passed**；保留 2 个 FastAPI/Starlette 测试依赖弃用提醒，不影响通过。
- 前端全部 **81 passed**，类型检查与生产构建通过。
- `npm audit`：包含开发依赖 **0** 个已知漏洞。
- `pip-audit`：扫描当前虚拟环境 **66** 个包，**0** 个已知漏洞；`pip check` 通过。
- Git whitespace 检查通过。
- 新增 37 个后端安全回归、3 个前端会话回归。覆盖匿名拦截、登录限流、CSRF、跨站无正文写入、会话轮换/到期/退出、HTTPS/Host、流式超限、知识库额度、压缩 PDF、解析超时、异常日志去敏。已有 Word/PDF 功能回归通过。
- 浏览器预览工具两次连接超时，未完成视觉验收；临时模拟预览服务已停止。前端已通过编译及客户端行为测试，真实部署需复查登录页显示和浏览器 Cookie 行为。

## 上线前仍需完成

按 [安全上线说明](PRODUCTION_DEPLOYMENT.md) 配置实际域名、访问密码哈希、HTTPS 证书、反向代理、防火墙及独立数据目录。此次只准备配置模板，没有在真实服务器上验证 Nginx/systemd、证书、DNS 或 Linux 资源限制。

当前登录者共享全部资料，适合受信任人员使用；**不能将共享密码发给公众作为多用户服务**。公众注册需实现独立账户和各接口的数据归属授权，并改用跨实例会话/限流与队列。当前上线入口固定单 worker，重启会话失效。

Windows 文档解析有超时、并发与格式大小限制，没有 Linux 的地址空间硬上限；生产模板针对 Linux。安全扫描只覆盖检查时公布的依赖漏洞，不代表不存在未知漏洞。
