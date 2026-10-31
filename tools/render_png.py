"""Quick PNG of a board region for reviewing placement and routing without KiCad's GUI.

    python3 tools/render_png.py BOARD X0 Y0 X1 Y1 OUT.png [scale px/mm]   (page coordinates, mm)

F.Cu red, B.Cu blue, In2 yellow, USB pairs magenta (F) / cyan (B), vias grey, pads orange
outlines, rule-area keepouts white. HOT=net1,net2 (env) fills those nets' pads green.
"""
import os
import sys

import pcbnew
from PIL import Image, ImageDraw

pcb, x0, y0, x1, y1, out = sys.argv[1], *map(float, sys.argv[2:6]), sys.argv[6]
S = float(sys.argv[7]) if len(sys.argv) > 7 else 20
HOT = set(filter(None, os.environ.get('HOT', '').split(',')))
b = pcbnew.LoadBoard(pcb)
im = Image.new('RGB', (int((x1 - x0) * S), int((y1 - y0) * S)), (20, 20, 20))
d = ImageDraw.Draw(im, 'RGBA')


def P(v):
    return (pcbnew.ToMM(v.x) - x0) * S, (pcbnew.ToMM(v.y) - y0) * S


col = {pcbnew.F_Cu: (220, 60, 60, 170), pcbnew.B_Cu: (60, 120, 230, 170), pcbnew.In2_Cu: (230, 200, 40, 140)}
for z in b.Zones():
    if z.GetIsRuleArea():
        o = z.Outline().COutline(0)
        d.polygon([P(o.CPoint(i)) for i in range(o.PointCount())], outline=(255, 255, 255, 200))
for t in b.GetTracks():
    if t.GetClass() == 'PCB_VIA':
        p = P(t.GetPosition())
        r = pcbnew.ToMM(t.GetWidth(pcbnew.F_Cu)) / 2 * S
        d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=(200, 200, 200, 220))
    elif t.GetLayer() in col:
        c = col[t.GetLayer()]
        if t.GetNetname().startswith('USB_DN'):
            c = (255, 0, 255, 230) if t.GetLayer() == pcbnew.F_Cu else (0, 255, 255, 230)
        d.line([P(t.GetStart()), P(t.GetEnd())], fill=c, width=max(1, int(pcbnew.ToMM(t.GetWidth()) * S)))
for f in b.GetFootprints():
    for p in f.Pads():
        bb = p.GetBoundingBox()
        hot = p.GetNetname() in HOT or p.GetNetname().split('/')[-1] in HOT
        d.rectangle([P(bb.GetOrigin()), P(bb.GetEnd())], outline=(255, 160, 0, 255),
                    fill=(0, 255, 0, 255) if hot else None)
    try:
        cb = f.GetCourtyard(pcbnew.F_CrtYd).BBox()
        d.rectangle([P(cb.GetOrigin()), P(cb.GetEnd())], outline=(120, 120, 255, 120))
    except Exception:
        pass
    px, py = P(f.GetPosition())
    if 0 <= px < im.width and 0 <= py < im.height:
        d.text((px, py), f.GetReference(), fill=(255, 255, 255, 255))
im.save(out)
