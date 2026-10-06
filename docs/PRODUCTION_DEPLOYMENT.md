# 私有工作区安全上线

此版本适用于自己或受信任人员使用。访问密码持有人共享全部任务、原文、知识库与修改权限；不要把密码公开给陌生人。若要开放公众注册，必须先实现独立账户、任务/知识库归属权限和账户额度，现有登录不能代替用户隔离。

## 准备配置

1. 使用独立的发布目录与数据目录，例如 `/srv/verifier/app`、`/srv/verifier/www`、`/srv/verifier/data`。网页目录只放前端构建产物，不放项目根目录、`.env`、数据库、备份或日志。
2. 后端使用 Python 3.13 的独立虚拟环境，安装前升级 pip 到 `26.2.1` 或更高修复版本，再安装项目依赖；前端使用 Node 22.12+、24 或 26（以 `frontend/package.json` engines 为准），用 `npm ci` 按锁文件安装。
3. 在后端目录执行 `python scripts/hash_password.py`，交互输入至少 16 个字符的随机访问密码，复制输出的哈希到服务器环境配置。不要将明文密码放在命令行、前端代码、`VITE_` 变量或版本库里。哈希也按机密配置保存。
4. 使用实际域名替换下方例子。Origin 必须为完整 HTTPS origin，不能带结尾 `/`、路径或通配符。域名与 `VERIFIER_ALLOWED_HOSTS` 必须匹配。

```dotenv
VERIFIER_ENV=production
VERIFIER_ALLOWED_HOSTS=verifier.example.com
VERIFIER_ALLOWED_ORIGINS=https://verifier.example.com
VERIFIER_PASSWORD_HASH=<交互脚本输出的完整哈希>
VERIFIER_DATABASE_PATH=/srv/verifier/data/app.db
VERIFIER_KB_CACHE_DIR=/srv/verifier/data/knowledge-models
VERIFIER_PROVIDER_MODE=mock
VERIFIER_JUDGE_MODE=off
VERIFIER_KB_EMBEDDINGS=off
```

如需联网核验，再设置 Tavily、MiMo 模式和对应的服务器密钥，具体见 README；先验证离线登录流程。不要将本地演示资料自动带到服务器。备份数据库和环境配置只保存到受限目录。

## 构建与运行

在 `frontend` 执行 `npm ci`、`npm test`、`npm run build`、`npm audit`。构建使用 `emptyOutDir=false` 保留现有输出，因此发布应从**新的空目录**生成产物，例如 `npm run build -- --outDir ../release/frontend-20261006`，再将此次产物放到 `/srv/verifier/www`。不要从长期复用的 dist 目录混入旧资源。

在 `backend` 设置上述服务器环境变量后，执行：

```text
python scripts/serve_production.py
```

专用入口会检查 production 模式和访问配置，只监听 `127.0.0.1:8000`，使用一个 worker，关闭开发重载、WebSocket、访问日志和默认 Server 响应头。会话、提交额度和背景任务限流当前保存在单进程中；不要启动多 worker 或多实例。服务重启后需重新登录；修改密码哈希后重启会撤销全部现有会话。

在 Linux 可参考 [systemd 模板](../deploy/verifier.service.example)，先创建低权限 `verifier` 账户、虚拟环境和数据目录，调整路径。示例环境文件由 root 持有、权限 0600，数据目录由服务账户持有，代码目录不可写。systemd 限制整组服务内存为 2 GiB、CPU 为 2 核并限制进程数。启用本地向量模型时按实际模型需求评估内存，并保持上限。

## HTTPS 与静态文件

参考 [Nginx 模板](../deploy/nginx.conf.example)，将域名、证书和目录改为实际值。证书需提前取得并配置续期；模板不是自动签发工具。配置完成执行 `nginx -t`，通过后再加载配置。

模板在同一 HTTPS 域名下提供静态前端与 `/api/`：强制 TLS 1.2/1.3，固定转发 Host，覆盖用户传入的转发头，关闭 API 缓存，限制连接/请求频率和慢请求，隐藏敏感扩展名和目录，设置 CSP、禁止嵌入、禁嗅探和 Referrer 防护。CSP 允许现有 Vue 样式属性需要的内联 CSS，脚本仅允许本站资源。

只向公网开放 80/443；8000 和 Vite/Vitest 服务端口不得对公网开放。Uvicorn 仅信任本机 Nginx 的转发头，不使用 `forwarded_allow_ips='*'`。如已有负载均衡/CDN，需要按实际可信代理链调整，不直接信任用户提交的 `X-Forwarded-*`。必须由 HTTPS 反向代理提供接口，HTTP 请求会被应用拒绝。

## 已加入的保护

- 会话 Cookie 为 `__Host-verifier_session`，256 位随机值，HttpOnly、Secure、SameSite=Strict，最长 8 小时；服务端仅存 token 哈希，退出立即撤销。
- 所有业务接口均需登录；API 不缓存。写入需会话绑定的 CSRF token，并检查 Origin 和跨站标识。密码只在服务端用 scrypt 验证，参数 N=32768、r=8、p=3（符合 [OWASP scrypt 建议](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html#scrypt)）；每分钟最多 5 次登录尝试，所有客户端共享限制。
- 普通 API 写入最多 256 KiB，知识库导入最多 4 MiB，包括无 Content-Length 的分块请求；密码接口最多 8 KiB。
- 核验创建/重试与知识库导入/搜索/重建共同受每分钟 10 次、最多 3 个并发操作限制。此为私有工作区的保护额度，不能当作每用户计费隔离。
- PDF/Word 不在 Web 进程内解析；解析进程不继承供应商密钥，最多同时 2 个、15 秒超时后终止。Linux 单个解析进程还限制 512 MiB 地址空间与 10 秒 CPU 时间；Windows 依赖超时及格式大小限制，没有 Linux 的地址空间硬限制。
- PDF 单个解压流最多 2 MiB、所有流合计最多 8 MiB、100 页、10,000 个对象；Word 解压后最多 32 MiB、正文 XML 最多 8 MiB。复杂或超限文档可能被拒绝，可拆分或粘贴正文。
- 生产模式关闭 OpenAPI/Swagger/ReDoc。域名、HTTPS origin 或密码哈希缺失时拒绝启动。

## 实机验收

在正式服务器完成配置后再检查以下行为：

1. 用未登录浏览器打开网站，应先显示密码页；请求 `/api/knowledge/documents`、任务原文和导出应返回 401。
2. 登录后可以创建核验、导入/搜索资料并导出；HTTP 跳 HTTPS。Cookie 应带 HttpOnly、Secure、SameSite=Strict，业务响应应有 no-store。
3. 退出后刷新原任务页面需要重新登录；重放旧 Cookie 返回 401。跨站写入或缺少 CSRF header 返回 403。
4. `/.env`、`/.git/config`、数据库和源码不能下载；8000、5173 及测试工具端口从公网不能连接。
5. 在部署环境重新运行 `npm audit` 与 `python -m pip_audit`；本次扫描结果只代表检查时已安装依赖，并不保证未来无漏洞。

本次没有连接或修改实际云服务器，Nginx/systemd 模板未在服务器上启用。证书、DNS、防火墙、代理规则及 Linux 资源限制需在部署环境实机验收。
