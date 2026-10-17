"""Rasterize the approved, single SVG source. Requires rsvg-convert and Pillow."""
from pathlib import Path
import subprocess
from PIL import Image

root=Path(__file__).resolve().parents[1]
directory=root/'brand'
for size in [16,32,180,192,512]:
    name=f'favicon-{size}.png' if size in [16,32] else f'icon-{size}.png'
    subprocess.run(['rsvg-convert','-w',str(size),'-h',str(size),str(directory/'abandon.svg'),'-o',str(directory/name)],check=True)
    if size==180:
        image=Image.open(directory/name).convert('RGBA')
        background=Image.new('RGBA',image.size,'#F4EBDD');background.alpha_composite(image)
        background.convert('RGB').save(directory/'apple-touch-icon.png')
        (directory/name).unlink()
image=Image.open(directory/'icon-512.png').convert('RGBA')
image.save(directory/'favicon.ico',sizes=[(16,16),(32,32),(48,48)])
background=Image.new('RGBA',image.size,'#F4EBDD');background.alpha_composite(image)
background.convert('RGB').save(directory/'icon-maskable-512.png')
# All non-background foreground fits inside the maskable 80% diameter safe circle.
for y in range(512):
    for x in range(512):
        pixel=background.getpixel((x,y))[:3]
        if max(abs(a-b) for a,b in zip(pixel,(244,235,221)))>8:
            assert (x-255.5)**2+(y-255.5)**2 <= (512*.4)**2
print('Approved SVG exported at 16/32/180/192/512, ICO and maskable safe-area verified.')
