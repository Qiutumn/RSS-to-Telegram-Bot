# Telegram 话题支持与 OrbStack 部署

本 fork 在上游 RSStT 的基础上增加超级群组话题支持。

## 使用方式

1. 将机器人加入已开启「话题」的超级群组，授予发送消息和媒体的权限。
2. 管理员进入目标话题，发送 `/sub@你的机器人用户名 https://example.com/rss`。
3. 后续 RSS 文章、图片、视频、相册和分段消息均发送到该话题。

同一 RSS 可在多个话题独立订阅。在话题中执行 `/list`、`/unsub`、`/unsub_all`、`/set`、
`/activate_subs`、`/deactivate_subs`、`/import`、`/export`，仅操作该话题的订阅。
按钮翻页和确认操作保留话题范围，管理员权限仍按整个群组校验。

也可以在与机器人的私聊中指定目标：

```text
/sub -1001234567890:42 https://example.com/rss
/list -1001234567890:42
/set -1001234567890:42
/unsub -1001234567890:42 https://example.com/rss
```

公开群组可使用 `@群组用户名:42`。42 为话题 ID，可从该话题的消息链接获取。
机器人会校验群组是否启用话题，以及指定 ID 是否指向话题的创建消息。
如果机器人无法读取该创建消息，请直接在话题内执行订阅命令。
导入 OPML 时，在目标话题回复机器人的导入提示；私聊上传时在文件说明中填写 `-1001234567890:42`。

不带话题 ID 的远程命令操作 General（常规）话题。显式 ID `0` 和 `1` 均表示 General。
普通频道、非话题群组和私聊沿用原来的命令。

## 范围和异常处理

- `/set_default`、语言、订阅额度及权限仍是聊天级设置，影响整个群组。
- OPML 只导出当前话题的源与标题；导入到哪个话题，就在那里创建订阅。
- 关闭或删除话题后，收到 Telegram 的话题错误会暂停该话题的订阅，保留数据，不退出群组。
  重新打开话题后，在该话题执行 `/activate_subs` 恢复；暂停期间错过的文章不会自动补发。
- 现有订阅升级后留在 General。将旧订阅迁移到话题可先导出 OPML、在目标话题导入，确认后再删除原订阅。
- 支持 SQLite 和 PostgreSQL 自动升级。回滚请恢复升级前数据库备份；旧版无法表达同源多话题订阅。
- 此功能针对已存在的群组话题，不负责创建、删除或重命名话题。

## OrbStack

```bash
cp .env.orb.example .env
# 编辑 .env，填写 TOKEN 和 MANAGER
docker compose up -d --build
docker compose ps
docker compose logs --tail=80 bot
```

`TOKEN` 从 @BotFather 获取；`MANAGER` 为自己的 Telegram 数字用户 ID。
默认 `MULTIUSER=0` 是私人实例。根据上游权限模型，如需在群组中使用，建议由非匿名管理员发送命令；
其他用户和聊天可用管理员命令 `/user_info` 按需授权。
需要代理时，可在 `.env` 配置 `T_PROXY=socks5://host.docker.internal:10808`，端口按本机代理填写。

此部署从固定摘要的上游镜像复用依赖，再复制本 fork 的源码；不会直接运行未修改的上游代码。
订阅数据库与登录会话保存在本地 `config/`，容器重启后保留。`.env` 和 `config/` 不提交到 Git，
也不进入镜像构建上下文。不向宿主机公开端口，容器内置健康检查与日志轮转。

更新：`docker compose up -d --build`。停止：`docker compose down`（保留本地数据）。
备份：先停止机器人，再备份 `config/`；该目录含登录会话，应保密。

## 自动化验证

在项目根目录运行以下离线测试，不需要 Telegram 凭据：

```bash
docker build -f Dockerfile.orb -t rss-telegram-topics:local .
docker run --rm --network none --entrypoint python \
  -v "$PWD:/workspace:ro" rss-telegram-topics:local /workspace/tests/run.py
```

真实 Telegram 验收：分别在两个话题订阅一个测试 RSS，发布测试条目，检查两个话题均收到消息；
在其中一个话题退订后再次发布，确认仅另一个话题收到。再验证含图片、相册、长文以及关闭话题的行为。
