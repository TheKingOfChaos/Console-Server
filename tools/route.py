"""Route the placed board with Freerouting, then pour ground on the outer layers.

Stackup: F.Cu signal / In1.Cu GND plane / In2.Cu signal + +3.3V pour / B.Cu signal.
Net classes (from the .kicad_pro) set the widths: HighCurrent 1.2 mm for the 5 V path
to the Pi, HV_24V 0.8 mm, Power 0.5 mm, USB_90R 0.16 mm, Default 0.2 mm.

Run from the project directory after build_pcb.py:  python3 tools/route.py [passes]
Needs Java 25 and the Freerouting 2.4.x jar (FREEROUTING_JAR, default ~/.local/share/freerouting.jar).
"""
import os
import subprocess
import sys
import time

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
PCB = os.path.join(PROJ, 'usb-console-server.kicad_pcb')
JAR = os.environ.get('FREEROUTING_JAR', os.path.expanduser('~/.local/share/freerouting.jar'))
JAVA = os.environ.get('JAVA', '/usr/lib/jvm/java-25-openjdk-amd64/bin/java')
WORK = os.environ.get('ROUTE_WORK', os.path.join(PROJ, 'route'))


def mm(v):
    return pcbnew.FromMM(v)


def board_rect(b, inset):
    """Board outline rectangle (from build_pcb's constants; the board is a rounded rectangle)."""
    sys.path.insert(0, HERE)
    from build_pcb import OX, OY, W, H
    x1, y1, x2, y2 = OX + inset, OY + inset, OX + W - inset, OY + H - inset
    return [(mm(x1), mm(y1)), (mm(x2), mm(y1)), (mm(x2), mm(y2)), (mm(x1), mm(y2))]


def zone(b, layer, net, name, inset=0.6, clearance=0.3, priority=0):
    # never b.Remove() a zone: in KiCad 10's SWIG binding that corrupts the wrapper type table
    for z in b.Zones():
        if z.GetZoneName() == name:
            z.SetNet(b.FindNet(net))
            z.SetLocalClearance(mm(clearance))
            return z
    z = pcbnew.ZONE(b)
    z.SetLayer(layer)
    z.SetNet(b.FindNet(net))
    z.SetZoneName(name)
    for x, y in board_rect(b, inset):       # AppendCorner avoids the shared_ptr outline handle
        z.AppendCorner(pcbnew.VECTOR2I(int(x), int(y)), -1)
    z.SetMinThickness(mm(0.2))
    z.SetLocalClearance(mm(clearance))
    z.SetThermalReliefGap(mm(0.3))
    z.SetThermalReliefSpokeWidth(mm(0.4))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_THT_THERMAL)   # solid to SMD pads, relief on THT
    z.SetAssignedPriority(priority)
    # pour islands connected to nothing are dropped
    z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
    b.Add(z)
    return z


def pre_fanout(b, dia=0.5, drill=0.25, width=0.2, inset=0.7):
    """Locked inward stubs + vias for every GND pin of the FT4232Hs, placed before
    autorouting so no ground pin can be boxed in at 0.5 mm pitch."""
    n = 0
    for f in b.GetFootprints():
        if 'LQFP-64' not in f.GetFPID().GetLibItemName().wx_str():
            continue
        cx, cy = _mmxy(f.GetPosition())
        vias = []
        for p in f.Pads():
            if p.GetNetname() != 'GND':
                continue
            px, py = _mmxy(p.GetPosition())
            sx, sy = pcbnew.ToMM(p.GetSize().x), pcbnew.ToMM(p.GetSize().y)
            # inward along the pad's long axis, to just inside the pad tip
            if abs(px - cx) > abs(py - cy):
                half = max(sx, sy) / 2
                tip = (px - (half + inset) * (1 if px > cx else -1), py)
            else:
                half = max(sx, sy) / 2
                tip = (px, py - (half + inset) * (1 if py > cy else -1))
            near = [v for v in vias if (v[0] - tip[0]) ** 2 + (v[1] - tip[1]) ** 2 < 0.9 ** 2]
            end = near[0] if near else tip
            t = pcbnew.PCB_TRACK(b)
            t.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(px), pcbnew.FromMM(py)))
            t.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(end[0]), pcbnew.FromMM(end[1])))
            t.SetLayer(pcbnew.F_Cu)
            t.SetWidth(mm(width))
            t.SetNet(b.FindNet('GND'))
            t.SetLocked(True)
            b.Add(t)
            if not near:
                v = pcbnew.PCB_VIA(b)
                v.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(tip[0]), pcbnew.FromMM(tip[1])))
                v.SetDrill(mm(drill))
                v.SetWidth(mm(dia))
                v.SetNet(b.FindNet('GND'))
                v.SetLocked(True)
                b.Add(v)
                vias.append(tip)
            n += 1
    return n


def strip_all_copper(path):
    """Remove every track, arc and via from the saved board file (file level: removing vias
    through the Python binding corrupts it)."""
    out, block = [], None
    for line in open(path).read().split('\n'):
        if block is None and line in ('\t(segment', '\t(via', '\t(arc'):
            block = True
            continue
        if block is not None:
            if line == '\t)':
                block = None
            continue
        out.append(line)
    open(path, 'w').write('\n'.join(out))


def prepare(b):
    """Planes on the inner layers, nothing routed yet (copper already stripped at file level)."""
    b.SetLayerType(pcbnew.In1_Cu, pcbnew.LT_POWER)
    b.SetLayerType(pcbnew.In2_Cu, pcbnew.LT_SIGNAL)
    zone(b, pcbnew.In1_Cu, 'GND', 'GND_In1.Cu')
    print(f'pre-fanout: {pre_fanout(b)} FT4232H ground pins tied to the plane')
    if os.environ.get('USB_PAIRS_FIRST', '1') == '1':
        sys.path.insert(0, HERE)
        import diffpair
        rep = diffpair.route_all_2layer(b)
        if rep is None:
            raise SystemExit('USB pairs: could not route all four as coupled pairs')
        print('USB pairs routed first, locked:')
        for line in rep:
            print('   ', line)


def freeroute(dsn, ses, passes):
    cmd = [JAVA, '-jar', JAR, '--gui.enabled=false', '-de', dsn, '-do', ses, '-mp', str(passes),
           '-mt', str(max(1, (os.cpu_count() or 2) - 1))]
    if os.environ.get('FR_STRATEGY'):
        cmd += ['-us', os.environ['FR_STRATEGY']]
    t0 = time.time()
    log = open(os.path.join(WORK, 'freerouting.log'), 'w')
    subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True)
    print(f'freerouting finished in {time.time() - t0:.0f} s')


# ---------------------------------------------------------------- power copper

def _mmxy(v):
    return pcbnew.ToMM(v.x), pcbnew.ToMM(v.y)


def _pads(b):
    out = []
    for f in b.GetFootprints():
        for p in f.Pads():
            bb = p.GetBoundingBox()
            out.append(dict(ref=f.GetReference(), num=p.GetNumber(), net=p.GetNetname(),
                            x=pcbnew.ToMM(p.GetPosition().x), y=pcbnew.ToMM(p.GetPosition().y),
                            box=(pcbnew.ToMM(bb.GetLeft()), pcbnew.ToMM(bb.GetTop()),
                                 pcbnew.ToMM(bb.GetRight()), pcbnew.ToMM(bb.GetBottom())),
                            fcu=p.IsOnLayer(pcbnew.F_Cu)))
    return out


def _hull(pts):
    pts = sorted(set(pts))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, hi = [], []
    for p in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(hi) >= 2 and cross(hi[-2], hi[-1], p) <= 0:
            hi.pop()
        hi.append(p)
    return lo[:-1] + hi[:-1]


def _box_pts(boxes, grow):
    pts = []
    for x1, y1, x2, y2 in boxes:
        pts += [(x1 - grow, y1 - grow), (x2 + grow, y1 - grow), (x2 + grow, y2 + grow), (x1 - grow, y2 + grow)]
    return pts


POUR_POLYS = []


def _poly_zone(b, layer, net, name, poly, priority, clearance=0.3, solid=True):
    POUR_POLYS.append(poly)
    for z in b.Zones():
        if z.GetZoneName() == name:
            return z
    z = pcbnew.ZONE(b)
    z.SetLayer(layer)
    z.SetNet(b.FindNet(net))
    z.SetZoneName(name)
    for x, y in poly:
        z.AppendCorner(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)), -1)
    z.SetMinThickness(mm(0.25))
    z.SetLocalClearance(mm(clearance))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL if solid else pcbnew.ZONE_CONNECTION_THERMAL)
    z.SetAssignedPriority(priority)
    b.Add(z)
    return z


def _capsule(a, c, half):
    import math as _m
    dx, dy = c[0] - a[0], c[1] - a[1]
    n = _m.hypot(dx, dy) or 1.0
    nx, ny = -dy / n * half, dx / n * half
    ex, ey = dx / n * half, dy / n * half
    return [(a[0] - ex + nx, a[1] - ey + ny), (c[0] + ex + nx, c[1] + ey + ny),
            (c[0] + ex - nx, c[1] + ey - ny), (a[0] - ex - nx, a[1] - ey - ny)]


def _seg_dist(px, py, x1, y1, x2, y2):
    import math as _m
    dx, dy = x2 - x1, y2 - y1
    L = dx * dx + dy * dy
    t = 0 if L == 0 else max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / L))
    return _m.hypot(px - x1 - t * dx, py - y1 - t * dy)


def _box_dist(px, py, box):
    import math as _m
    x1, y1, x2, y2 = box
    return _m.hypot(max(x1 - px, 0, px - x2), max(y1 - py, 0, py - y2))


def _in_poly(x, y, poly):
    n, c = len(poly), False
    for i in range(n):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            c = not c
    return c


def gnd_stitch(b, pads, pitch=4.0, dia=0.6, drill=0.3, gap=0.3):
    """Ground vias on a grid (ties every outer-layer pour island to the In1 plane) plus a
    few under each fine-pitch IC, where the pour reaches the inward ends of its GND pins."""
    import math as _m
    sys.path.insert(0, HERE)
    from build_pcb import OX, OY, W, H
    segs = []
    for t in b.GetTracks():
        if t.GetNetname() == 'GND':
            continue
        if t.Type() == pcbnew.PCB_VIA_T:
            x, y = _mmxy(t.GetPosition())
            segs.append((x, y, x, y, pcbnew.ToMM(t.GetWidth(pcbnew.F_Cu)) / 2))
        else:
            (x1, y1), (x2, y2) = _mmxy(t.GetStart()), _mmxy(t.GetEnd())
            segs.append((x1, y1, x2, y2, pcbnew.ToMM(t.GetWidth()) / 2))
    others = [p for p in pads if p['net'] != 'GND']
    r = dia / 2
    placed = []

    def ok(x, y, g=gap):
        if x < OX + 1.5 or y < OY + 1.5 or x > OX + W - 1.5 or y > OY + H - 1.5:
            return False
        if any(_in_poly(x, y, poly) for poly in POUR_POLYS):
            return False
        if any(_box_dist(x, y, p['box']) < r + g for p in others):
            return False
        if any(_seg_dist(x, y, *s[:4]) < r + s[4] + g for s in segs):
            return False
        return not any(_m.hypot(x - a, y - c) < dia + 0.4 for a, c in placed)

    def add(x, y):
        v = pcbnew.PCB_VIA(b)
        v.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
        v.SetDrill(mm(drill))
        v.SetWidth(mm(dia))
        v.SetNet(b.FindNet('GND'))
        b.Add(v)
        placed.append((x, y))
        segs.append((x, y, x, y, r))
    # under the FT4232Hs and the hub: dense grid in the free middle of the pad ring
    for f in b.GetFootprints():
        name = f.GetFPID().GetLibItemName().wx_str()
        if 'LQFP-64' in name or 'QFN-36' in name:
            cx, cy = _mmxy(f.GetPosition())
            for i in range(-3, 4):
                for j in range(-3, 4):
                    x, y = cx + i * 1.0, cy + j * 1.0
                    if ok(x, y, 0.2):
                        add(x, y)
    under_ic = len(placed)
    y = OY + 2.0
    while y < OY + H - 1.5:
        x = OX + 2.0
        while x < OX + W - 1.5:
            if ok(x, y):
                add(x, y)
            x += pitch
        y += pitch
    return under_ic, len(placed) - under_ic


def tidy(b):
    """Autorouter leftovers: stubs narrower than the fab minimum, and solid ground on the Pi socket."""
    fixed = 0
    for t in b.GetTracks():
        if t.Type() == pcbnew.PCB_TRACE_T and t.GetWidth() < mm(0.127):
            t.SetWidth(mm(0.127))
            fixed += 1
    for f in b.GetFootprints():
        if f.GetReference() == 'J2':
            for p in f.Pads():
                if p.GetNetname() == 'GND':
                    p.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_FULL)
    return fixed


def stitch(b, net, center, span, want, pads, inside, drill=0.4, dia=0.8, gap=0.35):
    """Through vias on `net` near `center`, only where every other net's copper on every
    layer stays `gap` away, and only inside polygon `inside` (so a pour picks them up)."""
    import math as _m
    tracks = [t for t in b.GetTracks()]
    segs = []
    for t in tracks:
        if t.GetNetname() == net:
            continue
        if t.Type() == pcbnew.PCB_VIA_T:
            x, y = _mmxy(t.GetPosition())
            segs.append((x, y, x, y, pcbnew.ToMM(t.GetWidth(pcbnew.F_Cu)) / 2))
        else:
            (x1, y1), (x2, y2) = _mmxy(t.GetStart()), _mmxy(t.GetEnd())
            segs.append((x1, y1, x2, y2, pcbnew.ToMM(t.GetWidth()) / 2))
    others = [p for p in pads if p['net'] != net]

    def inside_poly(x, y):
        n, c = len(inside), False
        for i in range(n):
            (x1, y1), (x2, y2) = inside[i], inside[(i + 1) % n]
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                c = not c
        return c
    cands = sorted(((center[0] + i * 0.5, center[1] + j * 0.5)
                    for i in range(-int(span / 0.5), int(span / 0.5) + 1)
                    for j in range(-int(span / 0.5), int(span / 0.5) + 1)),
                   key=lambda p: _m.hypot(p[0] - center[0], p[1] - center[1]))
    placed = []
    r = dia / 2
    for x, y in cands:
        if len(placed) >= want:
            break
        if not inside_poly(x, y):
            continue
        if any(_box_dist(x, y, p['box']) < r + gap for p in others):
            continue
        if any(_seg_dist(x, y, *s[:4]) < r + s[4] + gap for s in segs):
            continue
        if any(_m.hypot(x - a, y - c) < dia + 0.3 for a, c in placed):
            continue
        v = pcbnew.PCB_VIA(b)
        v.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
        v.SetDrill(mm(drill))
        v.SetWidth(mm(dia))
        v.SetNet(b.FindNet(net))
        b.Add(v)
        placed.append((x, y))
        segs.append((x, y, x, y, r))
    return placed


def power_copper(b):
    """Copper pours that carry the real current; the routed traces only guarantee connectivity."""
    pads = _pads(b)
    by_net = {}
    for p in pads:
        by_net.setdefault(p['net'], []).append(p)

    def near(net, ref_pad, radius):
        return [p for p in by_net.get(net, []) if p['fcu'] and
                ((p['x'] - ref_pad['x']) ** 2 + (p['y'] - ref_pad['y']) ** 2) ** 0.5 <= radius]
    report = []
    # buck switch nodes: SW pin, catch diode, inductor, boot cap on F.Cu
    for net, name in (('/Power/SW5', 'SW5'), ('/Power/SW3', 'SW3')):
        ps = [p for p in by_net.get(net, []) if p['fcu']]
        if ps:
            _poly_zone(b, pcbnew.F_Cu, net, f'{name}_F', _hull(_box_pts([p['box'] for p in ps], 0.6)), 10)
            report.append(f'{name} pour')
    # buck inputs: +24V around each VIN pin
    for ic in ('U8', 'U9'):
        vin = next((p for p in by_net.get('+24V', []) if p['ref'] == ic), None)
        if vin:
            ps = near('+24V', vin, 9)
            _poly_zone(b, pcbnew.F_Cu, '+24V', f'VIN_{ic}_F', _hull(_box_pts([p['box'] for p in ps], 0.6)), 9)
            report.append(f'+24V input pour at {ic}')
    # +5V: buck output area, ideal-diode input area, and an In2 band between them
    l_out = None
    for p in by_net.get('+5V', []):
        if p['ref'].startswith('L'):
            l_out = p
    q1 = [p for p in by_net.get('+5V', []) if p['ref'] in ('Q1', 'U19')]
    if l_out and q1:
        out_ps = near('+5V', l_out, 16)
        out_poly = _hull(_box_pts([p['box'] for p in out_ps], 0.6))
        _poly_zone(b, pcbnew.F_Cu, '+5V', 'P5V_OUT_F', out_poly, 9)
        qx = sum(p['x'] for p in q1) / len(q1)
        qy = sum(p['y'] for p in q1) / len(q1)
        q_poly = _hull(_box_pts([p['box'] for p in q1] + [(qx - 3, qy - 2.5, qx + 3, qy + 2.5)], 0.4))
        _poly_zone(b, pcbnew.F_Cu, '+5V', 'P5V_DIODE_F', q_poly, 9)
        ox = sum(p['x'] for p in out_ps) / len(out_ps)
        oy = sum(p['y'] for p in out_ps) / len(out_ps)
        _poly_zone(b, pcbnew.In2_Cu, '+5V', 'P5V_BAND_In2', _capsule((ox, oy), (qx, qy), 3.5), 5)
        a = stitch(b, '+5V', (ox, oy), 6, 8, pads, out_poly)
        c = stitch(b, '+5V', (qx, qy), 4, 6, pads, q_poly)
        report.append(f'+5V band on In2 with {len(a)} + {len(c)} stitching vias')
    return report


def finish(b):
    """+3.3V pour in the free space of In2, ground pours on the outer layers, fill everything."""
    for line in power_copper(b):
        print(line)
    print(f'{tidy(b)} sub-minimum stubs widened')
    a, c = gnd_stitch(b, _pads(b))
    print(f'GND stitching: {a} vias under fine-pitch ICs, {c} on the grid')
    zone(b, pcbnew.In2_Cu, '+3.3V', '3V3_In2.Cu', inset=0.6, clearance=0.3, priority=0)
    zone(b, pcbnew.F_Cu, 'GND', 'GND_F.Cu', inset=0.6, clearance=0.3, priority=0)
    zone(b, pcbnew.B_Cu, 'GND', 'GND_B.Cu', inset=0.6, clearance=0.3, priority=0)
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())


def main():
    passes = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    os.makedirs(WORK, exist_ok=True)
    dsn, ses = os.path.join(WORK, 'board.dsn'), os.path.join(WORK, 'board.ses')
    strip_all_copper(PCB)
    b = pcbnew.LoadBoard(PCB)
    prepare(b)
    pcbnew.SaveBoard(PCB, b)
    b = pcbnew.LoadBoard(PCB)
    if not pcbnew.ExportSpecctraDSN(b, dsn):
        raise SystemExit('DSN export failed')
    if os.environ.get('EXPORT_ONLY'):
        print('exported', dsn)
        return
    freeroute(dsn, ses, passes)
    if not pcbnew.ImportSpecctraSES(b, ses):
        raise SystemExit('SES import failed')
    finish(b)
    pcbnew.SaveBoard(PCB, b)
    tracks = [t for t in b.GetTracks() if t.Type() == pcbnew.PCB_TRACE_T]
    vias = [t for t in b.GetTracks() if t.Type() == pcbnew.PCB_VIA_T]
    print(f'{len(tracks)} track segments, {len(vias)} vias')


if __name__ == '__main__':
    main()
