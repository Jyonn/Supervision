# Frienden 普通账户基础聊天（2026-10-24）

此文替代旧委托 OAuth 聊天说明。本实现使用普通账户 API；实际发布状态见交付记录。

## 账户接入

Supervision 自身邮箱登录保持独立。导师提交空间 slug、官方账户昵称和密码；学生在邀请固定的空间提交个人昵称和密码。后端使用当前 sermo-backend 的 `/spaces/lookup`、`/spaces/join`、`/users/me`、`/spaces/me`、`/users/refresh`，不访问任何 Frienden OAuth 接口。

`/spaces/join` 使用 `new_user_intent=check`，避免昵称拼写错误导致注册新账号。官方身份既检查 `official`，也检查空间 `official_user.user_id`。身份及空间来自经过认证的提供方响应，不从浏览器声明或本地解码 JWT 获取。

密码一次性使用，不保存。访问与刷新令牌仅以版本 2 加密 envelope 存储在 ChatCredential 中；vault 在项目目录外，目录 0700、key 文件 0600。旧 OAuth envelope 必须重新连接；不迁移或扩大旧授权。断开连接删除本地令牌，不向 Frienden 发全局登出请求。

## 基础功能与边界

- `GET /api/groups/:id/chat/status/`：当前空间账户状态。
- `POST/DELETE /api/groups/:id/chat/connection/`：昵称密码连接／本地断开。
- `GET /api/groups/:id/chat/conversations/`：本研究组可见会话及已连接的本组成员。
- `POST /api/groups/:id/chat/conversations/`：通过 `peer_id` 开始本组成员私聊，提供方继续执行好友等业务限制。
- `GET/POST /api/groups/:id/chat/messages/`：历史与文字消息。会话键绑定本地用户、研究组、凭据 generation 与有效时间；每次读取/发送都重新检查提供方成员列表。

提供方路径为 `/chats/`、`/chats/direct`、`/messages/`。主动请求 compact-v1，兼容普通 JSON、gzip 和两种压缩记录结构。每页 50 条、内存最多 500 条；每 5 秒读取最新页，支持手动同步和读取更早记录。近期快照会重新核对已删除/不可见消息；更早记录以再次读取时的提供方结果为准。

群聊须已在 Frienden 建立，且当前参与者全部映射到本组。这里不建群、不做群管理、好友申请或媒体操作；空间全员群与投稿会话不展示。发送结果不明时保留草稿和同一 client_message_id，不自动重发。不同账号/研究组/会话不复用消息和草稿；不持久化聊天内容到浏览器存储。

## 验证

```sh
npm test
.venv/bin/python server/manage.py test core
ABANDON_PROVIDER_FIXTURE=1 ABANDON_PROVIDER_SOURCE=/path/to/sermo-backend .venv/bin/python server/manage.py test core.test_provider_integration
.venv/bin/python server/manage.py check
.venv/bin/python server/manage.py makemigrations --check --dry-run
python3 scripts/build-pages.py
```

实际提供方联调通过独立进程与 `Sermo.isolated_test_settings` 运行，断言 SQLite 为 `:memory:`。普通登录、身份核验、私聊、文字发送与去重、压缩历史分页、刷新均使用真实提供方实现；内容安全和通知发送使用测试替身，不连接线上服务，不创建真实账号或消息。
