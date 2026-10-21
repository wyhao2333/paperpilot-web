# Ubuntu 部署 Agent Prompt

将以下内容交给 Ubuntu 电脑上的部署 Agent：

---

请在这台 Ubuntu 24.04 电脑部署 PaperPilot Web，源码仓库：
https://github.com/wyhao2333/paperpilot-web

先读取仓库的 AGENTS.md、README.md、docs/DEPLOY_UBUNTU.md 和 docs/AGENT_HANDOFF.md，然后实际完成 Docker Compose 本机部署，再接入我已有的 Cloudflare Tunnel。程序使用外部翻译 API，不需要 GPU 或 CUDA。

执行要求：

1. 检查 Docker Engine、Compose 插件、磁盘、8765 端口和现有服务。在我指定的目录部署；未指定时使用 ~/apps/paperpilot-web。复用已有部署时先检查数据卷和配置，不能覆盖它们或影响其他应用。
2. 克隆仓库并记录准确 commit；从 .env.example 创建新的 .env，仅在文件不存在时创建。先使用 APP_ORIGIN=http://localhost:8765、APP_SECURE_COOKIES=false、LOCAL_ADMIN_AUTOLOGIN=false、DAILY_TEXT_LIMIT=0。使用 docker compose up -d --build，实际检查容器状态、健康检查及 http://localhost:8765/api/health。
3. 端口只绑定 127.0.0.1:8765；保持单应用实例。使用 Compose 持久数据卷，保留非 root、只读根文件系统等已有设置。国内下载失败时根据实际报错处理依赖网络，不写死代理地址，也不要把 Tunnel 当作 API 出站代理。
4. 不开放注册，只创建一个初始管理员，后续成员由我在管理员界面添加。使用 docker compose exec paperpilot python -m paperpilot.admin create admin --admin，让我在终端隐藏输入管理员密码；账号已存在时不要重复创建或自行重置。不要把密码放进命令参数、聊天、日志或仓库。
5. 仓库不含我的本机数据和 API Key。不要从其他项目、浏览器、聊天记录或全局配置中搜集凭证。我会在网页中自行填写 API Key，可分别选择翻译模型和问答 / AI 重排模型。没有配置真实 API 时，明确记录该项尚未验证，不能用模拟结果代替。
6. 先用仓库的合成示例检查读取、双栏顺序、图表、公式、右侧 PDF 放大、缩放与 Esc、笔记和阅读进度。经我配置接口后，再以少量段落验证真实翻译、独立问答和 Enter 发送。不要自动执行全文翻译或 AI 重排来消耗额度。重启容器后检查数据仍在。
7. 本机通过后，检查已有 cloudflared 的运行方式和现有路由，只为本应用增加入口。使用我明确提供的域名，不猜测域名，也不覆盖已有 Tunnel 路由。若域名尚未给出，先完成本机部署，再集中告诉我需要提供哪项信息。
8. cloudflared 在宿主机运行时转发到 http://127.0.0.1:8765；若在容器运行，先配置可用的容器网络，不能把容器自己的 localhost 当作宿主机。公网使用 APP_ORIGIN=https://实际域名、APP_SECURE_COOKIES=true，并保持 LOCAL_ADMIN_AUTOLOGIN=false，然后重新创建应用容器。
9. 用实际 HTTPS 域名验证登录、PDF 上传和原页放大、手机访问、退出后不能匿名读取文献，以及账号间隔离。不使用 Cache Everything 缓存私人页面或 /api/*。不要把本机 HTTP 检查当作公网通过；启用 Secure cookie 后，本机 HTTP 不作为登录入口。
10. 保留已有文件和数据，不执行 docker compose down -v。备份时覆盖整个 /app/data，包括数据库、pdfs、media 和 secret.key，并存放在本机权限受控的备份目录。不要上传任何数据库、论文、密钥、Tunnel token 或 .env，也不要将包含凭证的配置全文输出。

请持续完成能够自主完成的部署工作；需要我隐藏输入密码或提供域名时，说明具体步骤。结束后报告：仓库 commit、部署目录、容器状态、localhost 健康检查、公网地址及验证结果、实际数据卷名、重启恢复结果，以及仍未验证的项目。所有输出均不得包含密码或任何密钥。
