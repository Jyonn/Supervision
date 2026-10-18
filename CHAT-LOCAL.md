# 聊天与通讯接入

当前消费端申请精确八项 scope：`profile email chat.read chat.write chat.modify contacts.read contacts.write contacts.modify`。无 phone、广场、投稿或个人修改范围。命名是 modify，不是 update。

导师新建/初次绑定与原空间续验采用管理员组合授权；只有确实启用消费端功能时才申请八项。学生加入固定邀请绑定的整数 space_id；导师 bootstrap 不传 space_id，由 Frienden 显式同意时冻结。已有成员从“聊天→我的连接”本人重新同意。旧 grant 不修改；旧窄范围密文也不能用于新动作。所有完整 token 在服务器独立 chat-vault 中加密，封装本地用户、研究组、space/sub、范围及凭据代次，不发给浏览器。

“会话与通讯”提供本研究组映射成员的会话、好友/申请、精确查找。本人显式确认发好友申请，接受/拒绝本人收到的申请；现有好友可私聊。有提供方建群资格的导师可显式建群（也可仅群主）。选中群后按照提供方实时 allowed_actions 提供邀请/移除、转让群主、改名、已解锁背景、有限时禁言/解除、自己退群；另有本人置顶与已读。群主角色、空间策略、好友关系仍由提供方每次执行校验，不因 scope 放宽。退友、群删除、媒体上传、永久禁言、消息删除/撤回均不开放。

只显示当前研究组已经映射同一空间身份的成员及其会话，排除全空间群与其他组成员；私聊使用用户+研究组+凭据代次绑定、最长一小时的签名会话键。共享研究组默认群仍由导师显式选择。待处理群邀请仅展示当前研究组已配置的群。创建板凳研究组不自动替用户创建友间群或添加好友。

文字上限512字符，HTTP游标轮询（不是WebSocket），内存上限500条。切换会话/研究组、退出或失效清空消息、草稿和游标，取消在途读取；不写浏览器消息存储。正文只作纯文字安全转义。文字发送幂等UUID，结果不明只允许用户手动重试；建群/好友/群操作没有后台自动重试。断开先清本地密文再尝试远端撤销，远端不确定会如实提示。token最长一小时，无refresh。

生产启用条件：提供方补全协作接口的版本已发布，supervision-web新revision明确批准六项范围且旧grant不扩权，提供方总开关启用，再将消费端 `SUPERVISION_FRIENDEN_CHAT_ENABLED=1`。默认关闭；消费端开关不能代替提供方能力，也不代替真实用户同意。回退先关消费端及提供方总开关；保留数据库和已有身份授权。

验证：完整Django套件包含模拟提供方的安全契约测试；另有可选的实际提供方隔离联调，启动独立进程与双方SQLite内存库，使用提供方原生授权、token、scope、好友和群模型，仅虚构账号。测试涵盖管理员组合建组、指定空间学生加入、旧成员重新同意、好友申请接受、私聊文字/历史/增量、建群、角色动作、转让和退群后失效。原生登录token仅用于虚构提供方的本人同意步骤，消费端始终只接收OAuth opaque token；两者不转换。

实际提供方联调运行方式（明确指定源码和隔离Python，不使用部署settings）：

```sh
ABANDON_PROVIDER_FIXTURE=1 ABANDON_PROVIDER_SOURCE=/absolute/provider/source ABANDON_PROVIDER_PYTHON=/absolute/isolated/python .venv/bin/python server/manage.py test core.test_provider_integration --noinput
```

没有真实生产消息、好友申请、邀请、群管理或本人OAuth验收。Chrome扩展面板仍阻挡桌面/手机视觉验收，未绕过；新增授权截图在Library技能恢复后补看，不以OCR代替像素检查。
