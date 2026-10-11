"""Simplified 3D model of the HCTL HC-RJ45-059D-2x8 (16-port stacked RJ45 block, LCSC C46261623).
No vendor model exists (EasyEDA has none, no datasheet drawing found), so this is an envelope:
exact footprint outline (115.06 x 28.2 mm), ASSUMED 25.4 mm height (typical 2xN stacked RJ45),
16 plug openings on the front face at the footprint's port positions.

Model coordinates = footprint coordinates with y flipped (KiCad 3D: +y up), z up from the board.
Run with a Python that has cadquery:  python tools/rj45_model.py
"""
import os
import cadquery as cq

W_L, W_R = -57.53, 57.53          # footprint courtyard x
Y_BACK, Y_FRONT = 11.71, -16.49   # footprint y -11.71 (back) .. 16.49 (front, board edge), flipped
H = 25.4                          # ASSUMED body height
PITCH = 13.97
COLS = [PITCH * (i - 3.5) for i in range(8)]
ROWS = (6.9, 18.5)                # opening centres (z): lower and upper port
OPEN_W, OPEN_H, DEPTH = 11.8, 8.4, 16.0

body = cq.Workplane('XY').box(W_R - W_L, Y_BACK - Y_FRONT, H, centered=False).translate((W_L, Y_FRONT, 0))
for x in COLS:
    for z in ROWS:
        cut = cq.Workplane('XY').box(OPEN_W, DEPTH, OPEN_H).translate((x, Y_FRONT + DEPTH / 2 - 0.01, z))
        body = body.cut(cut)
        latch = cq.Workplane('XY').box(6.4, DEPTH, 2.4).translate(
            (x, Y_FRONT + DEPTH / 2 - 0.01, z + (OPEN_H / 2 + 1.1) * (-1 if z < H / 2 else 1)))   # latches outward
        body = body.cut(latch)
# two locating pegs under the body (NPTH at x = +/-48.89, footprint y 6.8)
for x in (-48.89, 48.89):
    body = body.union(cq.Workplane('XY').circle(1.6).extrude(-3.0).translate((x, -6.8, 0)))

out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   'lib', 'console_server.3dshapes', 'RJ45_HC-RJ45-059D-2x8.step')
cq.exporters.export(body, out)
print('wrote', out)
