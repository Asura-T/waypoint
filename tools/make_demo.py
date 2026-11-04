"""Generate the fictional demo office shipped in demo/ (not a real building).

Usage: python tools/make_demo.py      (needs Pillow; writes into ../demo)
"""
import json, os
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'demo')
PXM = 28                      # pixels per metre
W, H = 40, 24                 # metres
WALL = 6

def font(size):
    for name in ('DejaVuSans-Bold.ttf', '/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf', 'Arial.ttf'):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size)

# rooms per floor: (number, name, x0, y0, x1, y1) in metres
FLOORS = [
    ('demo-ground', 'DEMO Ground', 0.0, [
        ('G01', 'Reception', 1, 1, 13, 9), ('G02', 'Meeting room', 13, 1, 22, 9), ('G03', 'Server room', 22, 1, 28, 9),
        ('G04', 'Open office', 28, 1, 39, 15), ('G05', 'Kitchen', 1, 15, 10, 23), ('G06', 'Toilets', 10, 15, 15, 23),
        ('G07', 'Storage', 15, 15, 22, 23), ('G08', 'Office', 22, 15, 28, 23), ('G09', 'Office', 28, 15, 39, 23)]),
    ('demo-level-1', 'DEMO Level 1', 4.0, [
        ('101', 'Board room', 1, 1, 15, 9), ('102', 'Office', 15, 1, 22, 9), ('103', 'Network closet', 22, 1, 26, 9),
        ('104', 'Open office', 26, 1, 39, 15), ('105', 'Training room', 1, 15, 15, 23), ('106', 'Toilets', 15, 15, 20, 23),
        ('107', 'Print room', 20, 15, 26, 23), ('108', 'Office', 26, 15, 39, 23)]),
]

os.makedirs(os.path.join(ROOT, 'plans'), exist_ok=True)
floors, rooms = [], []
for fid, fname, elev, rs in FLOORS:
    img = Image.new('L', (W * PXM, H * PXM), 255)
    d = ImageDraw.Draw(img)
    # watermark first so walls draw over it
    d.text((W * PXM / 2, H * PXM / 2), 'DEMO DATA', fill=232, font=font(150), anchor='mm')
    P = lambda v: int(round(v * PXM))
    d.rectangle([P(0.5), P(0.5), P(W - 0.5), P(H - 0.5)], outline=0, width=WALL + 4)
    for no, name, x0, y0, x1, y1 in rs:
        d.rectangle([P(x0), P(y0), P(x1), P(y1)], outline=40, width=WALL)
        # door gap on the corridor side
        dy = P(y1) if y1 <= 15 else P(y0)
        dx = P((x0 + x1) / 2)
        d.rectangle([dx - 18, dy - WALL, dx + 18, dy + WALL], fill=255)
        d.text((P((x0 + x1) / 2), P((y0 + y1) / 2)), name, fill=110, font=font(22), anchor='mm')
        rooms.append({'id': f'demo-{fid}-{no}', 'floor': fid, 'no': no, 'name': f'Demo – {name}',
                      'x0': x0 + 0.15, 'y0': y0 + 0.15, 'x1': x1 - 0.15, 'y1': y1 - 0.15, 'demo': True})
    d.text((P(1.2), P(H - 1.1)), f'{fname} - fictional demo plan, not a real building', fill=90, font=font(18), anchor='ls')
    d.text((P(20), P(12)), 'Corridor', fill=150, font=font(20), anchor='mm')
    img.save(os.path.join(ROOT, 'plans', f'{fid}.png'), optimize=True)
    floors.append({'id': fid, 'name': fname, 'elev': elev, 'extent': [0, 0, W, H], 'plan': f'plans/{fid}.png', 'demo': True})

json.dump({'floors': floors}, open(os.path.join(ROOT, 'floors.json'), 'w'), indent=1)
json.dump({'version': 1, 'rooms': rooms}, open(os.path.join(ROOT, 'rooms.json'), 'w'), indent=1, ensure_ascii=False)
print(len(floors), 'floors,', len(rooms), 'rooms written to', os.path.abspath(ROOT))
