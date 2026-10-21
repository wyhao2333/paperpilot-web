# Ubuntu 24.04 / Docker 部署

面向普通 16GB 内存的单台 Ubuntu 电脑。翻译调用外部 API，不需要 NVIDIA 驱动或 CUDA。

## 1. 获取独立源码

克隆独立 Web 仓库，以下命令均在该仓库根目录执行：

```bash
git clone https://github.com/wyhao2333/paperpilot-web.git
cd paperpilot-web
```

原 Zotero 插件仓库不参与运行。

需要 Docker Engine 和 Docker Compose 插件，安装方式以 Docker 官方 Ubuntu 文档为准。确认 `docker compose version` 可用。

## 2. 本机启动

```bash
cp .env.example .env
docker compose up -d --build
docker compose ps
docker compose logs --tail=80 paperpilot
curl --fail http://localhost:8765/api/health
```

应用默认仅映射服务器回环端口 `127.0.0.1:8765`，用于浏览器本地测试或本机 cloudflared 转发。镜像以非 root 用户运行，数据写入名为 `paperpilot-data` 的 Compose 卷；实际卷名有 Compose 项目前缀。

`DAILY_TEXT_LIMIT=0` 为默认值，翻译、AI 重排和问答只记录用量，不受本站每日字数限额阻挡。若从旧版迁移已有 `.env`，把其中的 `DAILY_TEXT_LIMIT=200000` 改为 `0`，再重新创建容器。需要限制成员用量时才显式设置正整数；服务商的余额及限流独立计算。

本机原生运行 `python3 run.py` 时可以直接进入管理员空间。Docker 通过端口映射访问时，容器看到的是网桥地址，因此默认关闭自动登录；先用离线命令创建一个管理员：

```bash
docker compose exec paperpilot python -m paperpilot.admin create admin --admin
```

密码在终端隐藏输入。然后在本机页面用该账号登录，在「阅读服务设置 → 成员管理」添加成员，没有公开注册入口。不要把密码放进 shell 参数、部署文档或 GitHub。

如果迁移了已有本地数据，管理员已经存在，用原来的账号登录；原生自动生成的 `admin` 没有已知密码，部署前可设置密码：

```bash
docker compose exec paperpilot python -m paperpilot.admin reset-password admin
```

不要把网桥地址或 Tunnel 转发请求视为本机自动管理员；Compose 已固定 `LOCAL_ADMIN_AUTOLOGIN=false`。若采用原生 Ubuntu 服务接入 Tunnel，也需在 `.env` 设置 `LOCAL_ADMIN_AUTOLOGIN=false` 并用离线命令设置管理员密码。

首次构建需要下载 Python、Node 和依赖，国内网络可能影响时间。依赖源或代理只能根据真实网络报错调整，不应把本机代理地址写死在镜像中。

## 3. 配置 Cloudflare Tunnel

先通过本机验收，再把已有 Tunnel 的一个域名入口转发到：

```text
http://localhost:8765
```

若 cloudflared 运行在另一个容器，容器的 localhost 指向自身。可把它加入同一 Compose 网络并转发到 `http://paperpilot:8765`，或使用经过明确配置的宿主机访问方式。

然后修改 `.env`：

```dotenv
APP_ORIGIN=https://reader.your-domain.example
APP_SECURE_COOKIES=true
LOCAL_ADMIN_AUTOLOGIN=false
```

重新运行 `docker compose up -d`，从 HTTPS 域名重新登录。来源校验基于显式配置，不信任客户端提交的转发头。此时本机 HTTP 浏览器不会收到 Secure 会话 cookie；本机恢复测试需要临时改回本地 origin 和 `APP_SECURE_COOKIES=false`。

为私人页面和 `/api/*` 保留 `Cache-Control: no-store`，不要设置“Cache Everything”覆盖它。Tunnel 的公网入口和翻译 API 出站连接分别实测。

## 4. 如果需要 API 代理

可在 `.env` 设置 `HTTPS_PROXY`。容器中的 `127.0.0.1` 是容器自己；访问宿主机代理可使用已配置的 `host.docker.internal`，前提是代理确实允许容器连接该端口。不要照搬 Windows 的代理地址。

无代理时保持为空即可，优先实测国内服务商 API。

## 5. 更新、停止、备份

```bash
git pull --ff-only
docker compose up -d --build
```

停止服务：

```bash
docker compose stop
```

停止后再备份整个 `/app/data`，例如先在当前项目下创建 `backups` 目录，再使用 `docker compose cp paperpilot:/app/data ./backups/data-copy`。保存数据库、原 PDF、`media/` 和 `secret.key`；缺少密钥将无法解密接口配置。

`backups/` 已加入忽略规则，备份不进入源码仓库。不要在需要保留数据时执行 `docker compose down -v`。

Ubuntu 笔记本应保持供电，并根据使用需求配置合盖行为，避免机器睡眠后 Tunnel 和服务同时离线。

## 6. 部署验收

- 本机与 HTTPS 域名均能完成对应环境下的登录。
- 上传自己的单栏、双栏 PDF，核对段落顺序、上下标公式、矢量图、表格及原页高亮；旧文献可执行「更新基础重排」。
- 如需 AI 分段，先验证真实模型，再手动执行「AI 重排」，核对结果及恢复上个版本。
- 配置真实 API，按段翻译、全文翻译、停止后恢复均可用。
- 重新启动容器，文件、译文、笔记和阅读位置仍保留。
- 两个账号无法访问彼此文献的 PDF、导出、笔记或问答。
- 手机网络和校园网分别测试公网入口；检查实际 API 出站连通性。

当前 Windows 开发环境没有 Docker，本文件和 Dockerfile 已准备；容器构建和 Ubuntu 实机验收留待部署时执行。
