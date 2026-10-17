"""Deterministic public-only frontend bundle. No backend or local files copied."""
from pathlib import Path
import hashlib
import json
import shutil
import re

root = Path(__file__).resolve().parents[1]
out = root / 'dist'
if out.exists():
    shutil.rmtree(out)
(out / 'assets').mkdir(parents=True)
html = (root / 'account.html').read_text()
for name in ['ui.js', 'avatars.js', 'ui.css', 'workspace.css', 'style.css', 'account.css', 'account.js', 'calendar.js', 'markdown.js', 'workspace-art.svg']:
    text = (root / name).read_text()
    if name == 'account.js':
        calendar_version=hashlib.sha256((root/'calendar.js').read_bytes()).hexdigest()[:12]
        icon_version=hashlib.sha256((root/'brand/abandon.svg').read_bytes()).hexdigest()[:12]
        text=text.replace("script.src='/assets/calendar.js'",f"script.src='/assets/calendar.js?v={calendar_version}'")
        text=text.replace('/assets/abandon.svg',f'/assets/abandon.svg?v={icon_version}')
        text = text.replace('本地开发账号', '邮箱账号').replace('邮件验证待本机配置', '邮件验证暂未配置')
        text = text.replace('创建本地账号', '创建账号').replace('本地版本说明', '功能说明')
        text = text.replace('本地 Django 服务', '服务器').replace('独立本地客户端', '独立客户端')
        text = text.replace('邮件凭据由本人在本机配置后启用', '邮件服务配置后启用')
        text = text.replace('<br>服务重启后需重新登录。', '')
        text = text.replace('<a href="http://127.0.0.1:4173" target="_blank" rel="noopener">查看独立演示原型 ↗</a>', '')
        text = text.replace('<a class="local-link" href="http://127.0.0.1:4173" target="_blank" rel="noopener">独立演示原型 ↗</a>', '')
    (out / 'assets' / name).write_text(text)
(out / 'assets/runtime.js').write_text("window.SUPERVISION_CONFIG=Object.freeze({apiOrigin:'https://api.abandon.qijiong.work',production:true});\n")
for name in ['chronelle-icon-192.png','abandon.svg','favicon-16.png','favicon-32.png','favicon.ico','apple-touch-icon.png','icon-192.png','icon-512.png','icon-maskable-512.png']:
    shutil.copyfile(root/'brand'/name,out/'assets'/name)
webmanifest=json.loads((root/'site.webmanifest').read_text())
for icon in webmanifest['icons']:
    icon['src']+='?v='+hashlib.sha256((out/icon['src'].lstrip('/')).read_bytes()).hexdigest()[:12]
(out/'assets/site.webmanifest').write_text(json.dumps(webmanifest,ensure_ascii=False,indent=2)+'\n')
def versioned(match):
    name=match[2]
    version=hashlib.sha256((out/'assets'/name).read_bytes()).hexdigest()[:12]
    return match[1]+'/assets/'+name+'?v='+version+'"'
html=re.sub(r'((?:href|src)=")/assets/([^"?]+)"',versioned,html)
(out / 'index.html').write_text(html)
(out / '.nojekyll').write_text('')
# The custom domain is managed through the approved repository Pages setting.
manifest = {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(out.rglob('*')) if p.is_file()}
(out / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
print(f'Built {len(manifest)} allowlisted files in dist; no CNAME or secrets.')
