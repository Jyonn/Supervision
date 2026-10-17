"""Explicit loopback account app using user-approved Chronelle PKCE polling.

No registered client secret, production credentials, or HTTP callback is reused.
The official application URL is public identity metadata; the consent name and
purpose explicitly identify the local installation. The user grants access.
"""
from .settings import *
CHRONELLE_ENABLED = True
CHRONELLE_AUTH_ENABLED = True
CHRONELLE_API_ORIGIN = 'https://api.chronelle.jyonn.space'
CHRONELLE_AUTHORIZE_ORIGIN = 'https://chronelle.jyonn.space'
CHRONELLE_APP_URL = 'https://api.abandon.qijiong.work'
CHRONELLE_CALLBACK_URL = ''
CHRONELLE_APP_NAME = '板凳 · Abandon（本机8001）'
CHRONELLE_PURPOSE = '本机127.0.0.1:8001科研空间：本人日历事件管理与研究组预约；授权凭据仅保存于本机，组员可见范围另行选择；不发送邀请。'
