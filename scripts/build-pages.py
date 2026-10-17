"""Deterministic public-only frontend bundle. No backend or local files copied."""
from pathlib import Path
import hashlib
import json
import shutil

root = Path(__file__).resolve().parents[1]
out = root / 'dist'
if out.exists():
    shutil.rmtree(out)
(out / 'assets').mkdir(parents=True)
html = (root / 'account.html').read_text().replace('Supervision · 本地账号', 'Supervision')
(out / 'index.html').write_text(html)
for name in ['ui.js', 'ui.css', 'style.css', 'account.css', 'account.js', 'calendar.js', 'markdown.js', 'workspace-art.svg']:
    text = (root / name).read_text()
    if name == 'account.js':
        text = text.replace('本地开发账号', '邮箱账号').replace('邮件验证待本机配置', '邮件验证暂未配置')
        text = text.replace('创建本地账号', '创建账号').replace('本地版本说明', '功能说明')
        text = text.replace('本地 Django 服务', '服务器').replace('独立本地客户端', '独立客户端')
        text = text.replace('邮件凭据由本人在本机配置后启用', '邮件服务配置后启用')
        text = text.replace('<br>服务重启后需重新登录。', '')
        text = text.replace('<a href="http://127.0.0.1:4173" target="_blank" rel="noopener">查看独立演示原型 ↗</a>', '')
        text = text.replace('<a class="local-link" href="http://127.0.0.1:4173" target="_blank" rel="noopener">独立演示原型 ↗</a>', '')
    (out / 'assets' / name).write_text(text)
(out / 'assets/runtime.js').write_text("window.SUPERVISION_CONFIG=Object.freeze({apiOrigin:'https://api.supervision.qijiong.work',production:true});\n")
(out / '.nojekyll').write_text('')
# CNAME intentionally omitted until repository/Pages/DNS review is approved.
manifest = {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(out.rglob('*')) if p.is_file()}
(out / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
print(f'Built {len(manifest)} allowlisted files in dist; no CNAME or secrets.')
