import hashlib
import json
from pathlib import Path
from unittest.mock import patch
from django.http import Http404
from django.test import SimpleTestCase
from . import preset_avatars as avatars

class PresetAvatarTests(SimpleTestCase):
    def test_all_36_preserve_zero_based_index_and_recorded_hash(self):
        manifest = json.loads((avatars.ASSET_DIR / 'manifest.json').read_text())
        self.assertEqual(len(manifest['tiles']), 36)
        for index, tile in enumerate(manifest['tiles']):
            self.assertEqual(tile['index'], index)
            self.assertEqual(avatars.preset_path(index).name, f'{index+1:02}.png')
            image = avatars.preset_png(index)
            self.assertNotEqual(image, avatars.FALLBACK_PNG)
            self.assertEqual(hashlib.sha256(image).hexdigest(), tile['sha256'])
            response = avatars.preset_avatar_response(index)
            self.assertEqual(response['Content-Type'], 'image/png')
            self.assertEqual(response.content, image)

    def test_bad_index_never_selects_another_asset(self):
        for value in (-1, 36, True, '../01', None):
            with self.assertRaises(Http404): avatars.preset_png(value)

    def test_missing_or_invalid_local_raster_uses_safe_local_fallback(self):
        with patch.object(Path, 'read_bytes', side_effect=OSError):
            self.assertEqual(avatars.preset_png(0), avatars.FALLBACK_PNG)
        with patch.object(Path, 'read_bytes', return_value=b'not an image'):
            self.assertEqual(avatars.preset_png(35), avatars.FALLBACK_PNG)
