"""Local Chronelle preset rasters; stored indices remain zero based."""
import base64
import hashlib
from pathlib import Path
import struct
from django.http import Http404, HttpResponse

ASSET_DIR = Path(__file__).resolve().parent / 'assets' / 'avatars'
PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
# A neutral raster fallback, independent of disk assets and third-party services.
FALLBACK_PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aNxkAAAAASUVORK5CYII=')

def preset_path(preset):
    if type(preset) is not int or not 0 <= preset < 36:
        raise Http404
    return ASSET_DIR / f'{preset + 1:02}.png'

def preset_png(preset):
    path = preset_path(preset)
    try:
        data = path.read_bytes()
        if (not data.startswith(PNG_SIGNATURE) or len(data) < 24
                or struct.unpack('>II', data[16:24]) != (209, 209)):
            raise ValueError('Invalid bundled preset')
        return data
    except (OSError, ValueError):
        return FALLBACK_PNG

def preset_avatar_response(preset):
    data = preset_png(preset)
    return HttpResponse(data, content_type='image/png', headers={
        'Cache-Control': 'public, max-age=86400',
        'ETag': '"' + hashlib.sha256(data).hexdigest() + '"',
        'X-Content-Type-Options': 'nosniff',
    })
