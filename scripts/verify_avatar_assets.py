"""Offline raster QA: python with Pillow; no network or user data.
Checks the preserved Chronelle tiles against their original 6x6 sheet.
"""
from pathlib import Path
import hashlib
import json
from PIL import Image, ImageChops, ImageStat

root = Path(__file__).resolve().parents[1] / 'server/core/assets/avatars'
sheet_path = root / 'avatar-sheet-6x6.png'
sheet = Image.open(sheet_path).convert('RGB')
assert sheet.size == (1254, 1254)
cells = [sheet.crop((i%6*209, i//6*209, (i%6+1)*209, (i//6+1)*209)).resize((40,40)) for i in range(36)]
tiles = []
for index in range(36):
    path = root / f'{index+1:02}.png'
    tile = Image.open(path).convert('RGB')
    assert tile.size == (209,209)
    distances = [sum(ImageStat.Stat(ImageChops.difference(tile.resize((40,40)), cell)).mean) for cell in cells]
    assert min(range(36), key=distances.__getitem__) == index, f'Unexpected sheet mapping {index}'
    tiles.append({'index': index, 'file': path.name, 'row': index//6, 'column': index%6,
                  'width':209, 'height':209,'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
manifest = {'source': 'Chronelle public/avatars/01.png..36.png (byte-identical to Frienden v2)',
            'sheet_source': 'Frienden public/assets/avatars/v2/avatar-sheet-6x6.png',
            'sheet_sha256': hashlib.sha256(sheet_path.read_bytes()).hexdigest(),
            'sheet_size': [1254,1254], 'index_order': 'zero-based row-major 6x6',
            'note':'Existing distributed tiles are preserved unchanged. They have color differences from raw sheet crops; nearest-cell pixel comparison verifies all 36 positions without recreating artwork.',
            'tiles':tiles}
(root/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
print('PASS: 36 PNGs, 209×209, all 36 row-major sheet positions, SHA-256 manifest')
