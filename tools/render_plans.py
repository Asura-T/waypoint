"""Optional: turn AutoCAD-exported PDF floor plans into aligned Waypoint floors.

Each PDF is rendered to a PNG and placed on one shared metre grid using the
column-grid bubble labels printed on the drawings (e.g. "C"..."T" and "4"..."12"),
so every floor lines up in 3D even if the PDFs use different paper sizes or scales.

Usage:
  pip install pymupdf
  python tools/render_plans.py <config.json> <pdf_dir> <map_dir>

<map_dir> is one map's folder inside the Waypoint data folder, e.g. /data/maps/main
(create the map in the app first). The script writes <map_dir>/plans/<id>.png and
<map_dir>/floors.json; existing rooms and walls are kept. Most people can simply use
the app's "+ Floor" wizard instead, which reads PDFs directly.
Copy tools/render_plans.example.json, fill in your building, and keep the filled-in
copy out of git (for example inside the data folder).
"""
import json, os, sys
import pymupdf

if len(sys.argv) != 4:
    sys.exit(__doc__)
cfg = json.load(open(sys.argv[1]))
SRC, OUT = sys.argv[2], sys.argv[3]
PXM = cfg.get('pixels_per_metre', 28)     # keep the widest floor under ~4096 px for WebGL
MARGIN = cfg.get('margin_m', 14)
gx0, gx1 = cfg['grid_x']                  # two grid labels along the width, e.g. ["C", "T"]
gy0, gy1 = cfg['grid_y']                  # two grid labels along the depth, top then bottom, e.g. ["12", "4"]
ref_font = cfg.get('grid_font', 'Calibri')


def grid(page):
    """Centres of the grid bubble labels, found by the font they are drawn in."""
    g = {}
    for b in page.get_text('dict')['blocks']:
        for l in b.get('lines', []):
            for s in l['spans']:
                if s['font'] == ref_font:
                    x0, y0, x1, y1 = s['bbox']
                    g.setdefault(s['text'].strip(), ((x0 + x1) / 2, (y0 + y1) / 2))
    return g


# the reference floor fixes the metre grid: grid_x labels are grid_x_m metres apart
ref = grid(pymupdf.open(os.path.join(SRC, cfg['reference_pdf']))[0])
m_per_pt = cfg['grid_x_m'] / (ref[gx1][0] - ref[gx0][0])
refm = lambda k, axis: ref[k][axis] * m_per_pt

os.makedirs(os.path.join(OUT, 'plans'), exist_ok=True)
floors = []
for fl in cfg['floors']:
    page = pymupdf.open(os.path.join(SRC, fl['pdf']))[0]
    g = grid(page)
    sx = (refm(gx1, 0) - refm(gx0, 0)) / (g[gx1][0] - g[gx0][0])     # metres per pt on this sheet
    sy = (refm(gy1, 1) - refm(gy0, 1)) / (g[gy1][1] - g[gy0][1])
    ox, oy = refm(gx0, 0) - g[gx0][0] * sx, refm(gy0, 1) - g[gy0][1] * sy
    X0, X1 = refm(gx0, 0) - MARGIN, refm(gx1, 0) + MARGIN
    Y0, Y1 = refm(gy0, 1) - MARGIN, refm(gy1, 1) + MARGIN
    clip = pymupdf.Rect((X0 - ox) / sx, (Y0 - oy) / sy, (X1 - ox) / sx, (Y1 - oy) / sy)
    z = PXM * sx
    plan = f"plans/{fl['id']}.png"
    page.get_pixmap(matrix=pymupdf.Matrix(z, z), clip=clip, colorspace=pymupdf.csGRAY).save(os.path.join(OUT, plan))
    floors.append({'id': fl['id'], 'name': fl.get('name', fl['id']), 'elev': fl['elev'],
                   'extent': [round(v, 3) for v in (X0, Y0, X1, Y1)], 'plan': plan})
    print(fl['id'], 'done')

json.dump({'floors': floors}, open(os.path.join(OUT, 'floors.json'), 'w'), indent=1)
print('Wrote', len(floors), 'floors to', OUT, '- restart is not needed, just reload the page.')
