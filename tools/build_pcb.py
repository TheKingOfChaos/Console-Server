"""Build usb-console-server.kicad_pcb from the schematic netlist and place every part.

Netlist comes from kicad-cli (exactly what "Update PCB from Schematic" would use), so
footprint paths, references and nets are in sync with the schematic. Anchors (ICs,
connectors, mechanics) are placed by hand in zones; passives are placed by a greedy
pass next to the pin they serve. No copper is routed.

Run from the project directory:  python3 tools/build_pcb.py
"""
import json
import math
import os
import subprocess
import sys
import tempfile

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from sexp import parse, find, first  # noqa: E402

NAME = 'usb-console-server'
STOCK_FP = '/usr/share/kicad/footprints'
OX, OY = 30.0, 30.0          # board origin on the page
W, H = 138.0, 141.0          # board size, mm: RJ45 block + corner slots


def mm(v):
    return pcbnew.FromMM(v)


def P(x, y):
    return pcbnew.VECTOR2I(mm(OX + x), mm(OY + y))


def to_board(v):
    return pcbnew.ToMM(v.x) - OX, pcbnew.ToMM(v.y) - OY


# ------------------------------------------------------------------ netlist

def load_netlist():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, 'n.net')
        subprocess.run(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadsexpr', '-o', path,
                        os.path.join(PROJ, NAME + '.kicad_sch')], check=True, capture_output=True)
        root = parse(open(path).read())
    comps = {}
    for c in find(first(root, 'components'), 'comp'):
        ref = first(c, 'ref')[1]
        props = {first(p, 'name')[1]: (first(p, 'value')[1] if first(p, 'value') else '') for p in find(c, 'property')}
        fields = {}
        for f in find(first(c, 'fields') or [], 'field'):
            fields[first(f, 'name')[1]] = f[2] if len(f) > 2 and not isinstance(f[2], list) else ''
        sp = first(c, 'sheetpath')
        comps[ref] = dict(
            ref=ref, value=first(c, 'value')[1], footprint=first(c, 'footprint')[1] if first(c, 'footprint') else '',
            sheet=first(sp, 'names')[1], path=first(sp, 'tstamps')[1] + first(c, 'tstamps')[1], props=props,
            fields=fields,
            dnp='dnp' in props, exclude_bom='exclude_from_bom' in props)
    pinnet = {}
    for n in find(first(root, 'nets'), 'net'):
        name = first(n, 'name')[1]
        for node in find(n, 'node'):
            pinnet[(first(node, 'ref')[1], first(node, 'pin')[1])] = name
    return comps, pinnet


# ------------------------------------------------------------------ board

class Board:
    def __init__(self):
        self.b = pcbnew.CreateEmptyBoard()
        self.b.SetCopperLayerCount(4)
        self.nets = {}
        self.fps = {}
        self.boxes = []          # (ref, x1, y1, x2, y2) placed courtyards, board mm

    def net(self, name):
        if name not in self.nets:
            ni = pcbnew.NETINFO_ITEM(self.b, name)
            self.b.Add(ni)
            self.nets[name] = ni
        return self.nets[name]

    def add_footprint(self, c, pinnet):
        lib, name = c['footprint'].split(':')
        libdir = os.path.join(PROJ, 'lib', 'console_server.pretty') if lib == 'console_server' \
            else os.path.join(STOCK_FP, lib + '.pretty')
        fp = pcbnew.FootprintLoad(libdir, name)
        if fp is None:
            raise RuntimeError(f'footprint {c["footprint"]} not found for {c["ref"]}')
        fp.SetFPID(pcbnew.LIB_ID(lib, name))
        fp.SetReference(c['ref'])
        fp.SetValue(c['value'])
        fp.SetPath(pcbnew.KIID_PATH(c['path']))
        fp.SetSheetname(c['sheet'].strip('/').split('/')[-1] or 'Root')
        fp.SetSheetfile(c['props'].get('Sheetfile', ''))
        for k, v in c['fields'].items():
            if k in ('Footprint', 'Reference', 'Value'):
                continue
            if k == 'Datasheet':
                fp.GetField(pcbnew.FIELD_T_DATASHEET).SetText(v)
            elif k == 'Description':
                fp.GetField(pcbnew.FIELD_T_DESCRIPTION).SetText(v)
            else:
                f = pcbnew.PCB_FIELD(fp, pcbnew.FIELD_T_USER, k)
                f.SetText(v)
                f.SetVisible(False)
                f.SetLayer(pcbnew.F_Fab)
                fp.Add(f)
        fp.SetDNP(c['dnp'])
        fp.SetExcludedFromBOM(c['exclude_bom'])
        # through-hole parts block placement on both sides
        self.tht = getattr(self, 'tht', set())
        if any(p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH for p in fp.Pads()):
            self.tht.add(c['ref'])
        for pad in fp.Pads():
            key = (c['ref'], pad.GetNumber())
            if key in pinnet:
                pad.SetNet(self.net(pinnet[key]))
        fp.Reference().SetTextSize(pcbnew.VECTOR2I(mm(0.8), mm(0.8)))
        fp.Reference().SetTextThickness(mm(0.12))
        self.b.Add(fp)
        self.fps[c['ref']] = fp
        fp.SetPosition(P(W + 30, 0))   # parked until placed
        return fp

    # -- geometry helpers --------------------------------------------------
    def courtyard(self, fp):
        """Courtyard box grown to cover every pad (some vendor courtyards stop short of the pads)."""
        boxes = []
        for layer in (pcbnew.F_CrtYd, pcbnew.B_CrtYd):
            try:
                poly = fp.GetCourtyard(layer)
                if poly.OutlineCount():
                    boxes.append(poly.BBox())
            except Exception:
                pass
        for pad in fp.Pads():
            bb = pad.GetBoundingBox()
            bb.Inflate(mm(0.15))
            boxes.append(bb)
        if not boxes:
            boxes.append(fp.GetBoundingBox(False))
        x1 = min(pcbnew.ToMM(b.GetLeft()) for b in boxes) - OX
        y1 = min(pcbnew.ToMM(b.GetTop()) for b in boxes) - OY
        x2 = max(pcbnew.ToMM(b.GetRight()) for b in boxes) - OX
        y2 = max(pcbnew.ToMM(b.GetBottom()) for b in boxes) - OY
        return x1, y1, x2, y2

    def place(self, ref, x, y, rot=0, back=False):
        fp = self.fps[ref]
        if back and not fp.IsFlipped():
            fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
        fp.SetOrientationDegrees(rot)
        fp.SetPosition(P(x, y))
        box = self.courtyard(fp)
        self.boxes.append((ref, *box, 'both' if ref in self.tht else back))
        return box

    def collides(self, box, back=False, margin=0.2, ignore=(), ref=None):
        x1, y1, x2, y2 = box
        if x1 < 0.5 or y1 < 0.5 or x2 > W - 0.5 or y2 > H - 0.5:
            return True
        # test points must stay reachable: never under the Pi
        if ref and ref.startswith('TP') and getattr(self, 'pi_rect', None):
            a1, b1, a2, b2 = self.pi_rect
            if x1 < a2 + 1 and x2 > a1 - 1 and y1 < b2 + 1 and y2 > b1 - 1:
                return True
        for ref, a1, b1, a2, b2, side in self.boxes:
            if ref in ignore or (side != back and side != 'both'):
                continue
            if x1 < a2 + margin and x2 > a1 - margin and y1 < b2 + margin and y2 > b1 - margin:
                return True
        return False

    def pad_pos(self, ref, num):
        fp = self.fps[ref]
        for pad in fp.Pads():
            if pad.GetNumber() == num:
                return to_board(pad.GetPosition())
        raise KeyError((ref, num))


# ------------------------------------------------------------------ placement plan

def plan(bd, comps):
    """Hand placement of anchors: the board is the RJ45 block wide plus a slotted
    chassis hole each side. Top band: hub + power (left), Pi HAT zone (right).
    Middle band: four FT4232H clusters over their jacks, chain connectors in the
    edge strips beside them. Bottom: the RJ45 block, front flush with the edge."""
    pl = bd.place
    rj_x, rj_y = W / 2, H - 16.49
    pl('J5', rj_x, rj_y)
    for j in range(8):
        cx = rj_x - 48.9 + 13.97 * j
        silk(bd, f'P{2 * j + 2:02d} / P{2 * j + 1:02d}', cx, rj_y - 12.9, 0.9)
    # --- four FT4232H clusters above their jack columns (FT1 at the P01 end)
    y_max = rj_y - 20.0            # MAX3232E row, just above the jacks
    y_ft = y_max - 26.0            # FT4232H
    for k in range(4):
        cl = rj_x - 57.5 + 28.75 * k
        pl(f'U{k + 1}', cl + 14.4, y_ft)                     # FT4232H
        pl(f'U{10 + 2 * k}', cl + 7.3, y_max, 90)            # MAX3232E, channels A/B
        pl(f'U{11 + 2 * k}', cl + 21.5, y_max, 90)           # MAX3232E, channels C/D
        ee_x = cl + 5.5 if abs(cl + 5.5 - 75.5) > 8 else cl + 21.5   # clear of the Pi standoffs
        pl(f'U{31 + k}', ee_x, y_ft - 12.0, 0)               # 93LC46B
        pl(f'X{2 + k}', cl + 4.2, y_ft - 3.5, 90)            # 12 MHz crystal at OSCI/OSCO
    # --- chain connectors in the edge strips beside the clusters
    pl('J8', W - 2.6, y_ft - 0.8, 0)      # signal link header, right edge
    pl('J3', W - 2.6, y_ft + 24.2, 0)       # 24 V chain header, right edge
    edge_socket(bd, 'J9', y_ft - 0.8)     # signal link socket, left edge
    edge_socket(bd, 'J4', y_ft + 24.2)      # 24 V chain socket, left edge
    # --- hub (top left), USB-C near the Pi's USB-A ports for a short cable
    pl('J1', 60, 4.2, 180)          # USB-C on the top edge
    pl('U22', 60, 13.5, 0)          # ESD array inline, between connector and hub
    pl('U5', 44, 18, 0)             # USB2514B
    pl('X1', 30, 9, 0)
    pl('U20', 13, 22, 90)           # hub config EEPROM
    # --- power (top left, below the hub)
    pl('U8', 14, 42, 0)             # TPS54560B, 5 V
    pl('U9', 42, 42, 0)             # TPS54360B, 3.3 V
    pl('C46', 63, 44, 0)            # 24 V bulk, 10.5 mm tall: kept out from under the Pi
    pl('Q2', 64, 56, 0)             # 24 V reverse-block FET
    pl('F2', 53, 56, 90)
    # --- Raspberry Pi: face down ON TOP of the board, USB/Ethernet hanging past the top edge,
    #     microSD slot facing up. Pi spans X 72..128, Y 0..65 (+ jacks to Y -22.6).
    m = pi_to_board(128.0, 65.0)
    place_pi_header(bd, m)
    for i, (lx, ly) in enumerate(PI_HOLES):
        pl(f'H{5 + i}', *m(lx, ly))
    pi_drawing(bd, m)
    bd.pi_map = m
    bd.pi_rect = (72.0, 0.0, 128.0, 65.0)
    # low parts (<= 3 mm) may sit under the Pi
    pl('Q1', 119, 58, 0)            # ideal-diode FET at the 5 V pins (2/4)
    pl('U19', 113, 58, 0)           # LM74700
    pl('D6', 105, 58, 90)           # SMAJ6.0A on PI_5V
    pl('U21', 113, 26, 0)           # HAT ID EEPROM near pins 27/28
    pl('U6', 94, 34, 0)             # ATtiny3216 housekeeping MCU
    pl('U18', 100, 12, 0)           # Pi console RS-232
    pl('U7', 84, 52, 0)             # LM317 for +3V3_MCU (SOT-223, 1.8 mm)
    # tall parts go in the strip right of the Pi
    pl('J7', 133, 13, 0)            # UPDI header
    pl('J6', 133, 24, 0)            # Pi console header
    pl(next(r for r, c in comps.items() if c['value'] == '470uF'), 133, 49, 90)   # PI_5V bulk
    pl(next(r for r, c in comps.items() if c['value'] == '220uF'), 133, 59, 90)
    # --- slotted chassis mounts in the four corners
    for ref, (x, y) in zip(('H1', 'H2', 'H3', 'H4'), ((5, 5), (5, H - 5), (W - 5, H - 5), (W - 5, 5))):
        pl(ref, x, y)


def edge_socket(bd, ref, y):
    """Right-angle socket on the left edge: body on the board, opening at the edge."""
    fp = bd.fps[ref]
    fp.SetOrientationDegrees(0)
    fp.SetPosition(P(0, y))
    x1 = bd.courtyard(fp)[0]
    bd.place(ref, 0.25 - x1, y)


# Raspberry Pi 4 in its own top-view coordinates (mm): 85 x 56, GPIO header along
# the y=0 long edge, pin 1 at the x=0 (microSD) end, USB/Ethernet at the x=85 end.
PI_HOLES = ((3.5, 3.5), (61.5, 3.5), (3.5, 52.5), (61.5, 52.5))


def pi_pin(n):
    n = int(n)
    col = (n - 1) // 2
    return 8.37 + 2.54 * col, (4.77 if n % 2 else 2.23)


def pi_to_board(x0, y0):
    """The Pi sits FACE DOWN on top of our board, turned so its USB/Ethernet end points
    past our top edge. Seen from above that is a mirror image of the Pi's own top view:
    board X = x0 - pi_y, board Y = y0 - pi_x."""
    return lambda lx, ly: (x0 - ly, y0 - lx)


def place_pi_header(bd, m):
    """Seat J2 (a socket on OUR top side) so pad n sits under Pi GPIO pin n."""
    want = {n: m(*pi_pin(n)) for n in ('1', '2', '3', '40')}
    fp = bd.fps['J2']
    for rot in (0, 90, 180, 270):
        fp.SetOrientationDegrees(rot)
        fp.SetPosition(P(0, 0))
        p1 = bd.pad_pos('J2', '1')
        dx, dy = want['1'][0] - p1[0], want['1'][1] - p1[1]
        fp.SetPosition(P(dx, dy))
        if all(math.dist(bd.pad_pos('J2', n), want[n]) < 0.01 for n in want):
            bd.boxes.append(('J2', *bd.courtyard(fp), False))
            return
    raise RuntimeError('no top-side orientation of J2 matches the face-down Pi pinout')


def pi_drawing(bd, m):
    """Pi outline, jack block, PoE-header keepout and a note on User.Drawings / F.Fab."""
    def poly(pts, layer, width=0.15):
        for a, b in zip(pts, pts[1:] + pts[:1]):
            sh = pcbnew.PCB_SHAPE(bd.b)
            sh.SetShape(pcbnew.SHAPE_T_SEGMENT)
            sh.SetStart(P(*a)); sh.SetEnd(P(*b))
            sh.SetLayer(layer); sh.SetWidth(mm(width))
            bd.b.Add(sh)
    rect = lambda x1, y1, x2, y2: [m(x1, y1), m(x2, y1), m(x2, y2), m(x1, y2)]
    poly(rect(0, 0, 85, 56), pcbnew.Dwgs_User)                 # Pi PCB
    poly(rect(64, 2, 87.6, 54), pcbnew.Dwgs_User)              # USB + Ethernet block, hangs past our edge
    poly(rect(56, 5, 66, 15), pcbnew.Dwgs_User)                # PoE header (approx.): keep clear
    poly(rect(0, 0, 85, 56), pcbnew.F_Fab, 0.1)
    cx, cy = m(32, 28)
    silk(bd, 'RASPBERRY PI 4 ABOVE, FACE DOWN, 11 mm standoffs', cx, cy, 1.2, pcbnew.Dwgs_User)
    silk(bd, 'parts under the Pi: 3 mm max height', cx, cy + 2.2, 1.0, pcbnew.Dwgs_User)
    px, py = m(61, 10)
    silk(bd, 'PoE hdr', px, py, 0.8, pcbnew.Dwgs_User)


def height_mm(fp):
    """Rough body height for the under-Pi check."""
    name = fp.GetFPID().GetLibItemName().wx_str()
    import re as _re
    mt = _re.search(r'CP_Elec_[\d.]+x([\d.]+)', name)
    if mt:
        return float(mt.group(1))
    for key, h in (('PinHeader', 8.5), ('PinSocket', 8.5), ('SRP1265A', 6.5), ('SRP7028A', 2.8), ('RJ45', 13.5),
                   ('USB_C', 3.3), ('SOIC-20W', 2.65), ('TO-252', 2.4), ('D_SMC', 2.3), ('D_SMA', 2.3),
                   ('SOT-223', 1.8), ('C_1210', 2.5)):
        if key in name:
            return h
    return 1.8


def check_under_pi(bd, m, limit=3.0):
    xs, ys = zip(*(m(0, 0), m(85, 56)))
    x1, x2, y1, y2 = min(xs), max(xs), max(min(ys), 0), max(ys)
    bad = []
    for ref, fp in bd.fps.items():
        if ref == 'J2' or ref.startswith('H'):
            continue
        bx1, by1, bx2, by2 = bd.courtyard(fp)
        if bx1 < x2 and bx2 > x1 and by1 < y2 and by2 > y1 and height_mm(fp) > limit:
            bad.append((ref, height_mm(fp)))
    return bad


def silk(bd, text, x, y, size=1.0, layer=pcbnew.F_SilkS):
    t = pcbnew.PCB_TEXT(bd.b)
    t.SetText(text)
    t.SetPosition(P(x, y))
    t.SetLayer(layer)
    t.SetTextSize(pcbnew.VECTOR2I(mm(size), mm(size)))
    t.SetTextThickness(mm(size * 0.15))
    if layer == pcbnew.B_SilkS:
        t.SetMirrored(True)
    bd.b.Add(t)


# ------------------------------------------------------------------ greedy passive placement

POWER = {'GND', '+3.3V', '+5V', '+24V', '+24V_RAW', '+3V3_MCU', 'PI3.3', 'PI_5V'}


def place_passives(bd, comps, pinnet, anchors):
    refs_by_sheet = {}
    for r, c in comps.items():
        refs_by_sheet.setdefault(c['sheet'], []).append(r)
    placed = set(b[0] for b in bd.boxes)
    claimed = set()
    pads_of = {}
    for r in comps:
        pads_of[r] = [(p.GetNumber(), pinnet.get((r, p.GetNumber()))) for p in bd.fps[r].Pads()]

    def center(ref):
        x1, y1, x2, y2 = bd.courtyard(bd.fps[ref])
        return (x1 + x2) / 2, (y1 + y2) / 2

    todo = [r for r in comps if r not in placed]
    # passives with a signal-net anchor first (they are the most constrained)
    def score(r):
        nets = {n for _, n in pads_of[r] if n}
        return 0 if nets - POWER else 1
    todo.sort(key=lambda r: (score(r), r))
    unplaced = []
    for r in todo:
        c = comps[r]
        nets = [n for _, n in pads_of[r] if n]
        same_sheet = set(refs_by_sheet[c['sheet']])
        cands = []
        for n in nets:
            for a in placed:
                if a.startswith(('H', '#')):
                    continue
                for num, an in pads_of.get(a, []):
                    if an != n:
                        continue
                    if n in POWER and (a, num) in claimed:
                        continue
                    if n == 'GND':
                        continue
                    pri = (0 if a in anchors else 1, 0 if a in same_sheet else 1, 1 if n in POWER else 0)
                    cands.append((pri, a, num, n))
        if not cands:
            unplaced.append(r)
            continue
        cands.sort(key=lambda t: t[0])
        best_pri = cands[0][0]
        group = [t for t in cands if t[0] == best_pri]
        ok = False
        for pri, a, num, n in group[:24]:
            px, py = bd.pad_pos(a, num)
            ax, ay = center(a)
            vx, vy = px - ax, py - ay
            if abs(vx) >= abs(vy):
                nx, ny = (1 if vx > 0 else -1), 0
            else:
                nx, ny = 0, (1 if vy > 0 else -1)
            rot = 0 if nx else 90
            fp = bd.fps[r]
            for d in (1.6, 2.6, 3.6, 4.8, 6.0, 7.5, 9.0, 11.0, 13.0, 15.0, 18.0):
                for lat in (0, 1.2, -1.2, 2.4, -2.4, 3.6, -3.6, 5.0, -5.0, 6.5, -6.5, 8.0, -8.0):
                    x = px + nx * d + (0 if nx == 0 else 0) + (lat if nx == 0 else 0)
                    y = py + ny * d + (lat if nx else 0)
                    fp.SetOrientationDegrees(rot)
                    fp.SetPosition(P(x, y))
                    box = bd.courtyard(fp)
                    if not bd.collides(box, ref=r):
                        bd.boxes.append((r, *box, False))
                        placed.add(r)
                        if n in POWER:
                            claimed.add((a, num))
                        ok = True
                        break
                if ok:
                    break
            if ok:
                break
        if not ok:
            unplaced.append(r)
    return unplaced


def place_near(bd, ref, cx, cy):
    fp = bd.fps[ref]
    spots = sorted(((cx + dx * 0.5, cy + dy * 0.5) for dx in range(-40, 41) for dy in range(-40, 41)),
                   key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)
    for x, y in spots:
        fp.SetOrientationDegrees(0)
        fp.SetPosition(P(x, y))
        box = bd.courtyard(fp)
        if not bd.collides(box, ref=ref):
            bd.boxes.append((ref, *box, False))
            return True
    return False


def spill(bd, refs, zone, pinnet=None):
    """Fallback for parts the greedy pass could not seat: search outward from the
    nearest pad they connect to (any non-GND net), closest free spot first."""
    x0, y0, x1, y1 = zone
    left = []
    placed = {b[0] for b in bd.boxes}
    for r in refs:
        fp = bd.fps[r]
        nets = {pinnet.get((r, p.GetNumber())) for p in fp.Pads()} - {None, 'GND'} if pinnet else set()
        targets = []
        for a in placed:
            for p in bd.fps[a].Pads():
                if pinnet.get((a, p.GetNumber())) in nets:
                    targets.append(to_board(p.GetPosition()))
        cx, cy = targets[0] if targets else ((x0 + x1) / 2, (y0 + y1) / 2)
        spots = [(cx + dx * 0.5, cy + dy * 0.5) for dx in range(-60, 61) for dy in range(-60, 61)]
        spots.sort(key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)
        done = False
        for x, y in spots:
            for rot in (0, 90):
                fp.SetOrientationDegrees(rot)
                fp.SetPosition(P(x, y))
                box = bd.courtyard(fp)
                if not bd.collides(box, ref=r):
                    bd.boxes.append((r, *box, False))
                    placed.add(r)
                    done = True
                    break
            if done:
                break
        if not done:
            left.append(r)
    return left


# ------------------------------------------------------------------ outline, planes, rules

def outline(bd):
    r = 2.0
    pts = [(r, 0), (W - r, 0), (W, r), (W, H - r), (W - r, H), (r, H), (0, H - r), (0, r)]
    segs = [((r, 0), (W - r, 0)), ((W, r), (W, H - r)), ((W - r, H), (r, H)), ((0, H - r), (0, r))]
    for a, b in segs:
        s = pcbnew.PCB_SHAPE(bd.b)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(P(*a))
        s.SetEnd(P(*b))
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetWidth(mm(0.1))
        bd.b.Add(s)
    for cx, cy, sx, sy in ((r, r, 0, r), (W - r, r, W - r, 0), (W - r, H - r, W, H - r), (r, H - r, r, H)):
        s = pcbnew.PCB_SHAPE(bd.b)
        s.SetShape(pcbnew.SHAPE_T_ARC)
        s.SetCenter(P(cx, cy))
        s.SetStart(P(sx, sy))
        s.SetArcAngleAndEnd(pcbnew.EDA_ANGLE(90, pcbnew.DEGREES_T), True)
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetWidth(mm(0.1))
        bd.b.Add(s)


def gnd_plane(bd, layer):
    z = pcbnew.ZONE(bd.b)
    z.SetLayer(layer)
    z.SetNet(bd.net('GND'))
    z.SetZoneName(f'GND_{bd.b.GetLayerName(layer)}')
    poly = z.Outline()
    poly.NewOutline()
    for x, y in ((0.6, 0.6), (W - 0.6, 0.6), (W - 0.6, H - 0.6), (0.6, H - 0.6)):
        poly.Append(mm(OX + x), mm(OY + y))
    z.SetMinThickness(mm(0.2))
    z.SetLocalClearance(mm(0.3))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_THT_THERMAL)
    bd.b.Add(z)
    return z


def rules(bd):
    ds = bd.b.GetDesignSettings()
    ds.m_MinClearance = mm(0.127)
    ds.m_TrackMinWidth = mm(0.127)
    ds.m_ViasMinSize = mm(0.45)
    ds.m_MinThroughDrill = mm(0.2)
    ds.m_HoleToHoleMin = mm(0.25)
    ds.m_CopperEdgeClearance = mm(0.3)
    ds.SetBoardThickness(mm(1.6))


def project_netclasses():
    pro = os.path.join(PROJ, NAME + '.kicad_pro')
    d = json.load(open(pro))
    d['meta']['filename'] = NAME + '.kicad_pro'
    base = dict(bus_width=12, clearance=0.15, diff_pair_gap=0.25, diff_pair_via_gap=0.25, diff_pair_width=0.2,
                line_style=0, microvia_diameter=0.3, microvia_drill=0.1, pcb_color='rgba(0, 0, 0, 0.000)',
                schematic_color='rgba(0, 0, 0, 0.000)', track_width=0.2, via_diameter=0.6, via_drill=0.3,
                wire_width=6, priority=2147483647)
    base.update(via_diameter=0.5, via_drill=0.25)   # JLCPCB 4-layer standard capability
    # Routing widths are what the smallest pad on each net can take (the autorouter
    # cannot neck down); the real current is carried by copper pours added in route.py.
    classes = [dict(base, name='Default'),
               dict(base, name='Power', track_width=0.3, clearance=0.15, via_diameter=0.6, via_drill=0.3, priority=4),
               dict(base, name='HighCurrent', track_width=0.6, clearance=0.2, via_diameter=0.8, via_drill=0.4,
                    priority=1),
               dict(base, name='HV_24V', track_width=0.5, clearance=0.3, via_diameter=0.6, via_drill=0.3, priority=2),
               dict(base, name='Chain24V', track_width=1.0, clearance=0.3, via_diameter=0.8, via_drill=0.4,
                    priority=0),
               dict(base, name='USB_90R', track_width=0.16, diff_pair_width=0.16, diff_pair_gap=0.15,
                    clearance=0.15, priority=3)]
    # KiCad netclass patterns are wildcards (* and ?) only; no [..] character classes
    pats = [('USB_90R', '*USB_*_D?'),
            ('Chain24V', '+24V_RAW'),
            ('HV_24V', '+24V'), ('HV_24V', '*/24V_FUSED'), ('HV_24V', '*/SW3'),
            ('HighCurrent', '+5V'), ('HighCurrent', 'PI_5V'), ('HighCurrent', '*/SW5'),
            ('Power', '+3.3V'), ('Power', 'GND'), ('Power', '+3V3_MCU'), ('Power', 'PI3.3'),
            ('Power', '*/VCORE'), ('Power', '*/VPLL'), ('Power', '*/VPHY'), ('Power', '*/VDDA33'),
            ('Power', '*/VBUS_UP')]
    d['net_settings'] = {
        'classes': classes,
        'meta': {'version': 4},
        'net_colors': None,
        'netclass_assignments': None,
        'netclass_patterns': [{'netclass': c, 'pattern': p} for c, p in pats],
    }
    # board design rules: JLCPCB 4-layer standard capability
    rules = d.setdefault('board', {}).setdefault('design_settings', {}).setdefault('rules', {})
    rules.update({'min_clearance': 0.127, 'min_track_width': 0.127, 'min_via_diameter': 0.45,
                  'min_via_annular_width': 0.1, 'min_through_hole_diameter': 0.2, 'min_hole_to_hole': 0.25,
                  'min_hole_clearance': 0.2, 'min_copper_edge_clearance': 0.3, 'min_microvia_diameter': 0.2,
                  'min_microvia_drill': 0.1, 'min_silk_clearance': 0.0, 'min_text_height': 0.6,
                  'min_text_thickness': 0.1})
    d.setdefault('text_variables', {})
    json.dump(d, open(pro, 'w'), indent=2)


# ------------------------------------------------------------------ main

def main():
    comps, pinnet = load_netlist()
    bd = Board()
    rules(bd)
    for c in comps.values():
        bd.add_footprint(c, pinnet)
    # the SMAJ6.0A on PI_5V: find it by value instead of guessing its reference
    d6 = next(r for r, c in comps.items() if c['value'] == 'SMAJ6.0A')
    plan_refs = {'D6': d6}
    orig_place = bd.place
    bd.place = lambda ref, *a, **k: orig_place(plan_refs.get(ref, ref), *a, **k)
    plan(bd, comps)
    bd.place = orig_place
    # chain connectors: pad n of the header must sit at the same height as pad n of the socket
    for hdr, skt in (('J3', 'J4'), ('J8', 'J9')):
        for pad in bd.fps[hdr].Pads():
            n = pad.GetNumber()
            assert abs(bd.pad_pos(hdr, n)[1] - bd.pad_pos(skt, n)[1]) < 0.01, (hdr, skt, n)
    # ground test points have no signal pin to anchor on: one by the bucks, one by the
    # USB-C input, one beside the jacks for a scope ground
    gnd_tps = {c['sheet']: r for r, c in comps.items() if r.startswith('TP') and c['value'] == 'GND'}
    for sheet, (cx, cy) in (('/Power/', (4.5, 58.0)), ('/USB hub/', (50.0, 4.0)), ('/Ports/', (131.5, 118.0))):
        if sheet in gnd_tps:
            place_near(bd, gnd_tps[sheet], cx, cy)
    anchors = {b[0] for b in bd.boxes}
    left, before = place_passives(bd, comps, pinnet, anchors), None
    while left and left != before:      # parts whose partners were not placed yet
        before, left = left, place_passives(bd, comps, pinnet, anchors)
    if left:
        print('spilled (no room near their pins):', left)
    left = spill(bd, left, (2, 2, W - 2, H - 2), pinnet) if left else left
    tall = check_under_pi(bd, bd.pi_map)
    print('tall parts under the Pi:', tall or 'none')
    left = []
    left = spill(bd, left, (2, 2, W - 2, H - 2), pinnet) if left else left
    outline(bd)
    for layer in (pcbnew.In1_Cu,):
        gnd_plane(bd, layer)
    silk(bd, 'USB CONSOLE SERVER  rev A  -  16 x RS-232 (Cisco console, rolled)', W / 2, 62, 1.5, pcbnew.B_SilkS)
    silk(bd, 'BOARD ID: [ ] 1  [ ] 2  [ ] 3   (ID straps on MCU sheet; I2C 0x30/0x31/0x32)', W / 2, 66, 1.2, pcbnew.B_SilkS)
    out = os.path.join(PROJ, NAME + '.kicad_pcb')
    project_netclasses()
    bd.b.SetFileName(out)
    pcbnew.SaveBoard(out, bd.b)
    # reload so the project's net classes apply, then fill the planes
    b2 = pcbnew.LoadBoard(out)
    pcbnew.ZONE_FILLER(b2).Fill(b2.Zones())
    pcbnew.SaveBoard(out, b2)
    print(f'{len(comps)} footprints, {len(bd.nets)} nets, unplaced: {left}')


if __name__ == '__main__':
    main()
