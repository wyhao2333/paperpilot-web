# 独立仓库发布与更新

本项目使用独立 Web 仓库：<https://github.com/wyhao2333/paperpilot-web>。

## 公开内容

只提交应用源码、依赖清单及锁文件、Docker 配置、通用文档、测试和自行生成的合成示例 PDF。不要导入父 Zotero 项目的 Git 历史。

以下内容只能保存在本机：

- `.env`、API Key、账号密码、加密密钥、Tunnel 凭证和私钥。
- 数据库、会话、使用记录、笔记、译文缓存、上传的论文及解析图片。
- 截图、日志、备份、机器专属配置、个人绝对路径和其他个人资料。
- 虚拟环境、Node 依赖、构建产物及临时工具。

`.env.example` 只提供非秘密的配置示例。`examples/paperpilot-sample.pdf` 是项目自行编写的合成测试文档，不是用户论文。

## 提交前检查

1. 检查 `.gitignore` 生效，用 `git diff --cached --name-only` 和 `git diff --cached --stat` 逐项检查暂存内容。
2. 对暂存文件运行秘密扫描；检查真实本地凭证是否出现在候选内容中，报告只能包含文件位置和规则名，不输出秘密值。
3. 检查文档、示例、文件名及提交作者邮箱，防止个人路径、论文标题或非公开联系方式进入仓库。
4. 运行后端测试与前端构建；使用独立分支推送并读取 Actions 结果。CI 通过不代替目标服务器和公网验收。
5. 不提交运行数据库、加密配置或密钥，即使它们已经加密。

Git 作者可使用账户的 GitHub noreply 邮箱；不要为了发布修改全局 Git 身份。

## 部署资料

- [Ubuntu / Docker 部署步骤](DEPLOY_UBUNTU.md)
- [部署 Agent 的执行约束](AGENT_HANDOFF.md)
- [可直接交给部署 Agent 的 prompt](DEPLOY_AGENT_PROMPT.md)
