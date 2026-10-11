"""Route the hub -> FT4232H USB pairs as coupled differential pairs.

route_all_bcu() (used by route.py): each pair drops to B.Cu next to each chip through a pair
of small vias, runs coupled on B.Cu, and gets a dedicated GND reference strip directly
above it on In2 (0.1 mm away) with a no-routing keepout, so it is a real microstrip over
solid ground. GND vias along both sides tie each strip to the In1 plane.

Older F.Cu-only notes:

Each pair is routed as ONE centreline (a fat path: both traces + gap + clearance), on
F.Cu only so it sits directly over the solid In1 GND plane, then split into D+ and D-
at +/- half the pair pitch. Short fan-in stubs join the 0.5 mm-pitch chip pins.
Downstream D+/D- are swapped at the hub pins (USB2514B PRTSP) so no pair crosses over.

Run on a saved board:  python3 tools/diffpair.py [port order, e.g. 4321]
"""
import heapq
import math
import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import maze  # noqa: E402
maze.RES = 0.1   # coarser grid for the long pair centrelines (obstacle growth stays conservative)
from close_gaps import strip_tracks, PCB  # noqa: E402

W = 0.16              # trace width
GAP = 0.15            # edge-to-edge gap inside the pair (~90 ohm on JLC04161H-3313)
HALF = (W + GAP) / 2  # centreline -> trace centreline
CLR = 0.15            # clearance to everything else
HUB = {1: ('1', '2'), 2: ('3', '4'), 3: ('6', '7'), 4: ('8', '9')}   # pins carrying (D+, D-) after the swap
FT_PINS = ('8', '7')                                                 # FT4232H (DP, DM)
ESCAPE = 1.0          # centreline start this far out from the pin row


def xy(v):
    return pcbnew.ToMM(v.x), pcbnew.ToMM(v.y)


def unit(v):
    n = math.hypot(*v)
    return (v[0] / n, v[1] / n)


def pin_pair(b, ref, pins):
    f = next(f for f in b.GetFootprints() if f.GetReference() == ref)
    pads = [next(p for p in f.Pads() if p.GetNumber() == n) for n in pins]
    c = xy(f.GetPosition())
    pts = [xy(p.GetPosition()) for p in pads]
    mid = ((pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2)
    d = (mid[0] - c[0], mid[1] - c[1])
    out = unit((1, 0) if abs(d[0]) > abs(d[1]) and d[0] > 0 else (-1, 0) if abs(d[0]) > abs(d[1])
               else (0, 1) if d[1] > 0 else (0, -1))
    half_len = max(pcbnew.ToMM(pads[0].GetSize().x), pcbnew.ToMM(pads[0].GetSize().y)) / 2
    tip = (mid[0] + out[0] * half_len, mid[1] + out[1] * half_len)
    return pads, pts, mid, out, tip


def centreline(b, nets, start, goal, margin=25.0):
    """A* for the pair centreline on F.Cu; obstacles grown by pair half-width + clearance."""
    x1, y1 = min(start[0], goal[0]) - margin, min(start[1], goal[1]) - margin
    x2, y2 = max(start[0], goal[0]) + margin, max(start[1], goal[1]) + margin
    from close_gaps import BOARD_BOX
    x1, y1, x2, y2 = max(x1, BOARD_BOX[0]), max(y1, BOARD_BOX[1]), min(x2, BOARD_BOX[2]), min(y2, BOARD_BOX[3])
    g = maze.Grid(b, nets, (x1, y1, x2, y2), 2 * (HALF + W / 2), CLR + 0.02, 0.5)
    blk = g.block[pcbnew.F_Cu]
    noisy = {'/Power/' + n for n in ('SW5', 'SW3', 'BOOT5', 'BOOT3', 'FB5', 'FB3', 'COMP5', 'COMP5_RC',
                                      'COMP3', 'COMP3_RC', 'RT5', 'RT3')}
    for f in b.GetFootprints():
        # keep the pairs clear of the bucks' switch nodes and their feedback/compensation networks
        if any(p.GetNetname() in noisy for p in f.Pads()):
            bb = f.GetBoundingBox(False)
            g._rect(blk, (pcbnew.ToMM(bb.GetLeft()), pcbnew.ToMM(bb.GetTop()),
                          pcbnew.ToMM(bb.GetRight()), pcbnew.ToMM(bb.GetBottom())), 1.0)
    si, sj = g.cell(*start)
    gi, gj = g.cell(*goal)
    for (i, j) in ((si, sj), (gi, gj)):
        r = int(0.45 / maze.RES)
        blk[max(i - r, 0):i + r + 1, max(j - r, 0):j + r + 1] = False
    moves = [(-1, 0, 1), (1, 0, 1), (0, -1, 1), (0, 1, 1), (-1, -1, 1.414), (-1, 1, 1.414), (1, -1, 1.414),
             (1, 1, 1.414)]
    openq = [(0.0, 0.0, (si, sj), None)]
    came, cost = {}, {(si, sj): 0.0}
    turn_cost = 0.6       # discourage wiggles: every direction change costs
    best = None
    while openq:
        _, c, node, d0 = heapq.heappop(openq)
        if node == (gi, gj):
            best = node
            break
        if c > cost.get(node, 1e18) + 1e-9:
            continue
        i, j = node
        for di, dj, w in moves:
            ni, nj = i + di, j + dj
            if not (0 <= ni < g.ny and 0 <= nj < g.nx) or blk[ni, nj]:
                continue
            c2 = c + w + (turn_cost if d0 is not None and (di, dj) != d0 else 0)
            if c2 < cost.get((ni, nj), 1e18):
                cost[(ni, nj)] = c2
                came[(ni, nj)] = (node, (di, dj))
                heapq.heappush(openq, (c2 + math.hypot(ni - gi, nj - gj), c2, (ni, nj), (di, dj)))
    if best is None:
        return None
    path = [best]
    while path[-1] in came:
        path.append(came[path[-1]][0])
    path.reverse()
    pts = [g.pos(i, j) for i, j in path]
    # keep only the corners
    simp = [pts[0]]
    for k in range(1, len(pts) - 1):
        a, p, c = simp[-1], pts[k], pts[k + 1]
        if abs((p[0] - a[0]) * (c[1] - p[1]) - (p[1] - a[1]) * (c[0] - p[0])) > 1e-9:
            simp.append(p)
    simp.append(pts[-1])
    return simp


def offset(poly, s):
    """Offset a polyline by s (positive = left of travel, y-down coordinates), mitred."""
    dedup = [poly[0]]
    for p in poly[1:]:
        if math.dist(p, dedup[-1]) > 1e-6:
            dedup.append(p)
    poly = dedup if len(dedup) > 1 else [poly[0], (poly[0][0] + 1e-3, poly[0][1])]
    out = []
    for k, p in enumerate(poly):
        if k == 0:
            d = unit((poly[1][0] - p[0], poly[1][1] - p[1]))
            n = (d[1], -d[0])
        elif k == len(poly) - 1:
            d = unit((p[0] - poly[k - 1][0], p[1] - poly[k - 1][1]))
            n = (d[1], -d[0])
        else:
            d1 = unit((p[0] - poly[k - 1][0], p[1] - poly[k - 1][1]))
            d2 = unit((poly[k + 1][0] - p[0], poly[k + 1][1] - p[1]))
            n1, n2 = (d1[1], -d1[0]), (d2[1], -d2[0])
            m = unit((n1[0] + n2[0], n1[1] + n2[1]))
            k_ = 1 / max(0.3, m[0] * n1[0] + m[1] * n1[1])
            n = (m[0] * k_, m[1] * k_)
        out.append((p[0] + n[0] * s, p[1] + n[1] * s))
    return out


def add_track(b, net, a, c):
    if math.dist(a, c) < 1e-4:
        return
    t = pcbnew.PCB_TRACK(b)
    t.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(a[0]), pcbnew.FromMM(a[1])))
    t.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(c[0]), pcbnew.FromMM(c[1])))
    t.SetLayer(pcbnew.F_Cu)
    t.SetWidth(pcbnew.FromMM(W))
    t.SetNet(b.FindNet(net))
    b.Add(t)


def poly_len(pts):
    return sum(math.dist(a, c) for a, c in zip(pts, pts[1:]))


def route_port(b, k):
    dp, dm = f'USB_DN{k}_DP', f'USB_DN{k}_DM'
    hpads, hpts, hmid, hout, htip = pin_pair(b, 'U5', HUB[k])
    fpads, fpts, fmid, fout, ftip = pin_pair(b, f'U{k}', FT_PINS)
    # the hub pins now carry (D+, D-) = HUB[k]: make the pad nets match the swapped schematic
    hpads[0].SetNet(b.FindNet(dp))
    hpads[1].SetNet(b.FindNet(dm))
    start = (htip[0] + hout[0] * ESCAPE, htip[1] + hout[1] * ESCAPE)
    goal = (ftip[0] + fout[0] * ESCAPE, ftip[1] + fout[1] * ESCAPE)
    c = centreline(b, {dp, dm}, start, goal)
    if not c:
        return None
    c = [htip] + c + [ftip]        # run the pair right up to each pin row
    left, right = offset(c, HALF), offset(c, -HALF)
    # which offset line meets D+ at the hub? (the same line must meet D+ at the FT4232H)
    lines = {}
    for line in (left, right):
        near_hub = min(((math.dist(line[0], hpts[i]), i) for i in (0, 1)))[1]
        near_ft = min(((math.dist(line[-1], fpts[i]), i) for i in (0, 1)))[1]
        if near_hub != near_ft:
            raise RuntimeError(f'port {k}: pair arrives mirrored; the hub swap is not in effect')
        lines[dp if near_hub == 0 else dm] = line
    for net, line in lines.items():
        i = 0 if net == dp else 1
        pts = [hpts[i]] + line[1:-1] + [fpts[i]]   # first/last offset points replaced by the pin centres
        for a, cc in zip(pts, pts[1:]):
            add_track(b, net, a, cc)
        lines[net] = pts
    return {n: poly_len(p) for n, p in lines.items()}, len(c)


def route_all_locked(b):
    """Route all four pairs before anything else exists, trying port orders until all fit,
    then lock them so the autorouter works around them. Returns report lines."""
    import itertools
    base = {t.m_Uuid.AsString() for t in b.GetTracks()}
    for order in itertools.permutations((1, 2, 3, 4)):
        report, ok = [], True
        added = []
        for k in order:
            r = route_port(b, k)
            if not r:
                ok = False
                break
            lens, corners = r
            lp, lm = lens[f'USB_DN{k}_DP'], lens[f'USB_DN{k}_DM']
            report.append(f'port {k}: D+ {lp:.1f} mm, D- {lm:.1f} mm, mismatch {abs(lp - lm):.2f} mm, '
                          f'{corners - 2} bends, F.Cu only, 0 vias')
        new = [t for t in b.GetTracks() if t.m_Uuid.AsString() not in base]
        if ok:
            for t in new:
                t.SetLocked(True)
            return ['order ' + ''.join(map(str, order))] + report
        # undo this order's tracks (tracks only: safe to remove)
        for t in new:
            b.Remove(t)
    return None


# ---------------------------------------------------------------- B.Cu pairs with an In2 GND strip

VIA_D, VIA_DR, VIA_OFF = 0.45, 0.2, 0.35      # transition vias: 0.45/0.2 mm, +/-0.35 mm from the pair centre
STRIP_HALF = 1.2                              # In2 GND strip half-width (pair half-width 0.235 + ~1 mm)


def _band_capsule(b):
    """Where route.py will put the +5V band on In2 (same construction as power_copper)."""
    import route
    pads = route._pads(b)
    p5 = [p for p in pads if p['net'] == '+5V']
    l_out = next((p for p in p5 if p['ref'].startswith('L')), None)
    q1 = [p for p in p5 if p['ref'] in ('Q1', 'U19')]
    if not l_out or not q1:
        return None
    out_ps = [p for p in p5 if p['fcu'] and math.dist((p['x'], p['y']), (l_out['x'], l_out['y'])) <= 16]
    o = (sum(p['x'] for p in out_ps) / len(out_ps), sum(p['y'] for p in out_ps) / len(out_ps))
    q = (sum(p['x'] for p in q1) / len(q1), sum(p['y'] for p in q1) / len(q1))
    return o, q, 3.5


def _block_capsule(g, blk, cap, grow):
    (ax, ay), (cx, cy), half = cap
    g._seg(blk, (ax, ay, cx, cy), half + grow)


def _add_via(b, net, p, d=VIA_D, dr=VIA_DR, locked=True):
    v = pcbnew.PCB_VIA(b)
    v.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(p[0]), pcbnew.FromMM(p[1])))
    v.SetWidth(pcbnew.FromMM(d))
    v.SetDrill(pcbnew.FromMM(dr))
    v.SetNet(b.FindNet(net))
    v.SetLocked(locked)
    b.Add(v)


def _add_trk(b, net, a, c, layer, locked=True):
    if math.dist(a, c) < 1e-4:
        return
    t = pcbnew.PCB_TRACK(b)
    t.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(a[0]), pcbnew.FromMM(a[1])))
    t.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(c[0]), pcbnew.FromMM(c[1])))
    t.SetLayer(layer)
    t.SetWidth(pcbnew.FromMM(W))
    t.SetNet(b.FindNet(net))
    t.SetLocked(locked)
    b.Add(t)


def _seg_rect(a, c, half, ext):
    d = unit((c[0] - a[0], c[1] - a[1]))
    n = (-d[1], d[0])
    a2 = (a[0] - d[0] * ext, a[1] - d[1] * ext)
    c2 = (c[0] + d[0] * ext, c[1] + d[1] * ext)
    return [(a2[0] + n[0] * half, a2[1] + n[1] * half), (c2[0] + n[0] * half, c2[1] + n[1] * half),
            (c2[0] - n[0] * half, c2[1] - n[1] * half), (a2[0] - n[0] * half, a2[1] - n[1] * half)]


def _zone(b, layer, net, poly, priority, rule_area=False, name=''):
    z = pcbnew.ZONE(b)
    z.SetLayer(layer)
    if rule_area:
        z.SetIsRuleArea(True)
        z.SetDoNotAllowTracks(True)
        z.SetDoNotAllowVias(False)
        z.SetDoNotAllowPads(False)
        z.SetDoNotAllowFootprints(False)
        z.SetDoNotAllowZoneFills(False)
    else:
        z.SetNet(b.FindNet(net))
        z.SetAssignedPriority(priority)
        z.SetMinThickness(pcbnew.FromMM(0.25))
        z.SetLocalClearance(pcbnew.FromMM(0.2))
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
        z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
    z.SetZoneName(name)
    for x, y in poly:
        z.AppendCorner(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)), -1)
    b.Add(z)


def _side_vias(centre, out, pts_sorted_dp_dm, mid):
    """Two transition vias side by side at `centre`, each on the side of its own pin."""
    lat = (-out[1], out[0])
    res = []
    for p in pts_sorted_dp_dm:
        s = 1 if (p[0] - mid[0]) * lat[0] + (p[1] - mid[1]) * lat[1] > 0 else -1
        res.append((centre[0] + lat[0] * VIA_OFF * s, centre[1] + lat[1] * VIA_OFF * s))
    return res


def route_all_bcu(b):
    """Route all four pairs (B.Cu + In2 GND strip), lock them. Returns report lines or None."""
    band = _band_capsule(b)
    noisy = {'/Power/' + n for n in ('SW5', 'SW3', 'BOOT5', 'BOOT3', 'FB5', 'FB3', 'COMP5', 'COMP5_RC',
                                      'COMP3', 'COMP3_RC', 'RT5', 'RT3')}
    sites = {}
    # 1) transition sites: hub side staggered outward (port k at 1.0 + 1.0*(k-1) mm), FT side 1.4 mm out
    for k in (1, 2, 3, 4):
        dp, dm = f'USB_DN{k}_DP', f'USB_DN{k}_DM'
        hpads, hpts, hmid, hout, htip = pin_pair(b, 'U5', HUB[k])
        hpads[0].SetNet(b.FindNet(dp))
        hpads[1].SetNet(b.FindNet(dm))
        fpads, fpts, fmid, fout, ftip = pin_pair(b, f'U{k}', FT_PINS)
        hc = (htip[0] + hout[0] * (1.0 + 1.0 * (k - 1)), htip[1] + hout[1] * (1.0 + 1.0 * (k - 1)))
        fc = (ftip[0] + fout[0] * 1.4, ftip[1] + fout[1] * 1.4)
        sites[k] = dict(dp=dp, dm=dm, hpts=hpts, hmid=hmid, hout=hout, htip=htip, hc=hc,
                        hv=_side_vias(hc, hout, hpts, hmid), fpts=fpts, fmid=fmid, fout=fout, ftip=ftip, fc=fc,
                        fv=_side_vias(fc, fout, fpts, fmid))
    # 2) F.Cu stubs pin -> straight out -> its via; and the vias themselves
    for k, st in sites.items():
        for (net, pins, vias, tip, out) in ((st['dp'], st['hpts'][0], st['hv'][0], st['htip'], st['hout']),
                                            (st['dm'], st['hpts'][1], st['hv'][1], st['htip'], st['hout']),
                                            (st['dp'], st['fpts'][0], st['fv'][0], st['ftip'], st['fout']),
                                            (st['dm'], st['fpts'][1], st['fv'][1], st['ftip'], st['fout'])):
            # run straight out along the pin to level with the via, then into it
            along = (vias[0] - pins[0]) * out[0] + (vias[1] - pins[1]) * out[1]
            knee = (pins[0] + out[0] * along, pins[1] + out[1] * along)
            _add_trk(b, net, pins, knee, pcbnew.F_Cu)
            _add_trk(b, net, knee, vias, pcbnew.F_Cu)
            _add_via(b, net, vias)
    # 3) B.Cu centrelines between the via-pair centres, each pair an obstacle for the next
    report = []
    for k in (4, 3, 2, 1, ):
        st = sites[k]
        s, g_ = st['hc'], st['fc']
        x1, y1 = min(s[0], g_[0]) - 25, min(s[1], g_[1]) - 25
        x2, y2 = max(s[0], g_[0]) + 25, max(s[1], g_[1]) + 25
        from close_gaps import BOARD_BOX
        x1, y1, x2, y2 = max(x1, BOARD_BOX[0]), max(y1, BOARD_BOX[1]), min(x2, BOARD_BOX[2]), min(y2, BOARD_BOX[3])
        grid = maze.Grid(b, {st['dp'], st['dm']}, (x1, y1, x2, y2), 2 * (HALF + W / 2), CLR + 0.02, 0.5)
        blk = grid.block[pcbnew.B_Cu]
        # keep the strip (not just the pair) clear of the +5V band, the bucks' noisy parts and holes
        if band:
            _block_capsule(grid, blk, band, STRIP_HALF + 0.3)
        for f in b.GetFootprints():
            if any(p.GetNetname() in noisy for p in f.Pads()):
                bb = f.GetBoundingBox(False)
                grid._rect(blk, (pcbnew.ToMM(bb.GetLeft()), pcbnew.ToMM(bb.GetTop()),
                                 pcbnew.ToMM(bb.GetRight()), pcbnew.ToMM(bb.GetBottom())), 0.5)
        for v in st['hv'] + st['fv']:
            i, j = grid.cell(*v)
            blk[max(i - 2, 0):i + 3, max(j - 2, 0):j + 3] = False
        for c_ in (s, g_):
            i, j = grid.cell(*c_)
            r = int(0.5 / maze.RES)
            blk[max(i - r, 0):i + r + 1, max(j - r, 0):j + r + 1] = False
        path = _astar(grid, blk, s, g_)
        if not path:
            return None
        c = path
        left, right = offset(c, HALF), offset(c, -HALF)
        # which line starts at the hub D+ via? it must also end at the FT D+ via
        lines = {}
        for line in (left, right):
            h = min(((math.dist(line[0], st['hv'][i]), i) for i in (0, 1)))[1]
            f_ = min(((math.dist(line[-1], st['fv'][i]), i) for i in (0, 1)))[1]
            if h != f_:
                raise RuntimeError(f'port {k}: pair arrives mirrored on B.Cu')
            lines[st['dp'] if h == 0 else st['dm']] = line
        lens = {}
        for net, line in lines.items():
            i = 0 if net == st['dp'] else 1
            pts = [st['hv'][i]] + line[1:-1] + [st['fv'][i]]
            for a, cc in zip(pts, pts[1:]):
                _add_trk(b, net, a, cc, pcbnew.B_Cu)
            stub_h = math.dist(st['hpts'][i], st['hv'][i]) + 0.3
            stub_f = math.dist(st['fpts'][i], st['fv'][i]) + 0.3
            lens[net] = poly_len(pts) + stub_h + stub_f
        # 4) In2 GND reference strip + keepout over the whole B.Cu run, GND vias along both sides
        for a, cc in zip(c, c[1:]):
            if math.dist(a, cc) < 0.05:
                continue
            _zone(b, pcbnew.In2_Cu, 'GND', _seg_rect(a, cc, STRIP_HALF, STRIP_HALF), 20, name=f'USB{k}_REF')
            _zone(b, pcbnew.In2_Cu, '', _seg_rect(a, cc, STRIP_HALF, STRIP_HALF), 0, rule_area=True,
                  name=f'USB{k}_KEEPOUT')
        lp, lm = lens[st['dp']], lens[st['dm']]
        report.append(f'port {k}: D+ {lp:.1f} mm, D- {lm:.1f} mm, mismatch {abs(lp - lm):.2f} mm, '
                      f'coupled on B.Cu over an In2 GND strip, 2 vias per line')
        st['centre'] = c
    # GND stitching beside each pair (both sides, every ~4 mm), clear of everything nearby
    n_gnd = _stitch_beside(b, [sites[k]['centre'] for k in sites if 'centre' in sites[k]])
    report.append(f'{n_gnd} GND vias stitched beside the pairs')
    return report


def _astar(g, blk, start, goal, turn_cost=0.6):
    si, sj = g.cell(*start)
    gi, gj = g.cell(*goal)
    moves = [(-1, 0, 1), (1, 0, 1), (0, -1, 1), (0, 1, 1), (-1, -1, 1.414), (-1, 1, 1.414), (1, -1, 1.414),
             (1, 1, 1.414)]
    openq = [(0.0, 0.0, (si, sj), None)]
    came, cost = {}, {(si, sj): 0.0}
    found = None
    while openq:
        _, c, node, d0 = heapq.heappop(openq)
        if node == (gi, gj):
            found = node
            break
        if c > cost.get(node, 1e18) + 1e-9:
            continue
        i, j = node
        for di, dj, w in moves:
            ni, nj = i + di, j + dj
            if not (0 <= ni < g.ny and 0 <= nj < g.nx) or blk[ni, nj]:
                continue
            c2 = c + w + (turn_cost if d0 is not None and (di, dj) != d0 else 0)
            if c2 < cost.get((ni, nj), 1e18):
                cost[(ni, nj)] = c2
                came[(ni, nj)] = (node, (di, dj))
                heapq.heappush(openq, (c2 + math.hypot(ni - gi, nj - gj), c2, (ni, nj), (di, dj)))
    if not found:
        return None
    path = [found]
    while path[-1] in came:
        path.append(came[path[-1]][0])
    path.reverse()
    pts = [g.pos(i, j) for i, j in path]
    simp = [pts[0]]
    for k in range(1, len(pts) - 1):
        a, p, c = simp[-1], pts[k], pts[k + 1]
        if abs((p[0] - a[0]) * (c[1] - p[1]) - (p[1] - a[1]) * (c[0] - p[0])) > 1e-9:
            simp.append(p)
    simp.append(pts[-1])
    simp[0], simp[-1] = start, goal
    return simp


def _stitch_beside(b, centres, lateral=0.95, pitch=4.0):
    pads = []
    for f in b.GetFootprints():
        for p in f.Pads():
            if p.GetNetname() == 'GND':
                continue
            bb = p.GetBoundingBox()
            pads.append((pcbnew.ToMM(bb.GetLeft()), pcbnew.ToMM(bb.GetTop()),
                         pcbnew.ToMM(bb.GetRight()), pcbnew.ToMM(bb.GetBottom())))
    segs = []
    for t in b.GetTracks():
        if t.GetNetname() == 'GND':
            continue
        if t.Type() == pcbnew.PCB_VIA_T:
            x, y = xy(t.GetPosition())
            segs.append((x, y, x, y, pcbnew.ToMM(t.GetWidth(pcbnew.F_Cu)) / 2))
        else:
            (x1, y1), (x2, y2) = xy(t.GetStart()), xy(t.GetEnd())
            segs.append((x1, y1, x2, y2, pcbnew.ToMM(t.GetWidth()) / 2))
    from route import _seg_dist, _box_dist
    placed, n = [], 0
    for c in centres:
        for a, cc in zip(c, c[1:]):
            L = math.dist(a, cc)
            if L < 1.0:
                continue
            d = unit((cc[0] - a[0], cc[1] - a[1]))
            nrm = (-d[1], d[0])
            t = pitch / 2
            while t < L:
                for sgn in (1, -1):
                    p = (a[0] + d[0] * t + nrm[0] * lateral * sgn, a[1] + d[1] * t + nrm[1] * lateral * sgn)
                    if any(_box_dist(*p, bx) < 0.3 + 0.2 for bx in pads):
                        continue
                    if any(_seg_dist(*p, *s_[:4]) < 0.3 + s_[4] + 0.15 for s_ in segs):
                        continue
                    if any(math.dist(p, q) < 1.0 for q in placed):
                        continue
                    _add_via(b, 'GND', p, 0.6, 0.3)
                    placed.append(p)
                    segs.append((*p, *p, 0.3))
                    n += 1
                t += pitch
    return n


# ---------------------------------------------------------------- two-layer coupled pairs

def _astar2(gF, gB, vblk, start, goal, via_cost=40, turn_cost=0.6, b_cost=1.15, straight=8, keep=15):
    """A* over (layer, i, j): F.Cu=0, B.Cu=1. A layer change only on a straight stretch: the pair
    must arrive and leave in the same direction for `straight` cells, and both transition vias
    (centre +/- perpendicular VIA_OFF) must clear every layer."""
    blks = (gF, gB)
    si, sj = start
    gi, gj = goal
    moves = [(-1, 0, 1), (1, 0, 1), (0, -1, 1), (0, 1, 1), (-1, -1, 1.414), (-1, 1, 1.414), (1, -1, 1.414),
             (1, 1, 1.414)]
    ny, nx = gF.shape
    off = VIA_OFF / maze.RES
    # state: (layer, i, j, direction, run) - run = straight cells in `direction` (capped), -k = must go
    # straight k more cells after a layer change
    start_n = (0, si, sj, None, 0)
    openq = [(0.0, 0.0, start_n)]
    cost = {start_n: 0.0}
    came = {}
    found = None
    while openq:
        _, c, node = heapq.heappop(openq)
        L, i, j, d0, run = node
        if (L, i, j) == (0, gi, gj) and run >= 0:
            found = node
            break
        if c > cost.get(node, 1e18) + 1e-9:
            continue
        for di, dj, w in moves:
            if run < 0 and (di, dj) != d0:
                continue                     # leaving a via pair: keep going straight
            ni, nj = i + di, j + dj
            if not (0 <= ni < ny and 0 <= nj < nx) or blks[L][ni, nj]:
                continue
            same = d0 is not None and (di, dj) == d0
            nrun = (run + 1 if run < 0 else min(run + 1, straight)) if same else 1
            c2 = c + w * (b_cost if L == 1 else 1) + (turn_cost if d0 is not None and not same else 0)
            n2 = (L, ni, nj, (di, dj), nrun)
            if c2 < cost.get(n2, 1e18):
                cost[n2] = c2
                came[n2] = node
                heapq.heappush(openq, (c2 + math.hypot(ni - gi, nj - gj), c2, n2))
        if d0 is not None and run >= straight and math.hypot(i - si, j - sj) > keep and math.hypot(i - gi, j - gj) > keep:
            n_ = math.hypot(*d0)
            pi_, pj_ = -d0[1] / n_ * off, d0[0] / n_ * off
            v1 = (int(round(i + pi_)), int(round(j + pj_)))
            v2 = (int(round(i - pi_)), int(round(j - pj_)))
            ok = all(0 <= a < ny and 0 <= c_ < nx and not vblk[a, c_] for a, c_ in (v1, v2))
            if ok and not blks[1 - L][i, j]:
                n2 = (1 - L, i, j, d0, -straight)
                c2 = c + via_cost
                if c2 < cost.get(n2, 1e18):
                    cost[n2] = c2
                    came[n2] = node
                    heapq.heappush(openq, (c2 + math.hypot(i - gi, j - gj), c2, n2))
    if not found:
        return None
    path = [found]
    while path[-1] in came:
        path.append(came[path[-1]])
    path.reverse()
    return [(L, i, j) for L, i, j, _, _ in path]


def _runs(grid, path):
    """Split an (L, i, j) path into per-layer runs of simplified points; returns [(layer, pts)]."""
    runs, cur, curL = [], [], None
    for L, i, j in path:
        p = grid.pos(i, j)
        if curL is None or L == curL:
            cur.append(p)
        else:
            runs.append((curL, cur))
            cur = [cur[-1], p] if cur[-1] != p else [p]
            cur = [p]
        curL = L
    runs.append((curL, cur))
    out = []
    for L, pts in runs:
        simp = [pts[0]]
        for k in range(1, len(pts) - 1):
            a, p, c = simp[-1], pts[k], pts[k + 1]
            if abs((p[0] - a[0]) * (c[1] - p[1]) - (p[1] - a[1]) * (c[0] - p[0])) > 1e-9:
                simp.append(p)
        if len(pts) > 1:
            simp.append(pts[-1])
        out.append((pcbnew.F_Cu if L == 0 else pcbnew.B_Cu, simp))
    return out


def clear_hub_corridor(b, depth=4.5, margin=0.6):
    """Move any footprint sitting in front of the hub's USB pins (1-9) just west of the
    corridor, to the nearest free spot. Returns the refs moved."""
    f5 = next(f for f in b.GetFootprints() if f.GetReference() == 'U5')
    pins = {p.GetNumber(): xy(p.GetPosition()) for p in f5.Pads()}
    p1, p9 = pins['1'], pins['9']
    half = max(pcbnew.ToMM(next(p for p in f5.Pads() if p.GetNumber() == '1').GetSize().x),
               pcbnew.ToMM(next(p for p in f5.Pads() if p.GetNumber() == '1').GetSize().y)) / 2
    tip = p1[0] - half
    corr = (tip - depth, min(p1[1], p9[1]) - margin, tip, max(p1[1], p9[1]) + margin)

    def box(f):
        bb = f.GetBoundingBox(False)
        return (pcbnew.ToMM(bb.GetLeft()), pcbnew.ToMM(bb.GetTop()), pcbnew.ToMM(bb.GetRight()),
                pcbnew.ToMM(bb.GetBottom()))

    def hit(a, c, g=0.0):
        return a[0] < c[2] + g and a[2] > c[0] - g and a[1] < c[3] + g and a[3] > c[1] - g
    movers = [f for f in b.GetFootprints() if f.GetReference() != 'U5' and not f.IsFlipped() and hit(box(f), corr)]
    moved = []
    for f in movers:
        others = [box(o) for o in b.GetFootprints() if o is not f and not o.IsFlipped()]
        x0, y0 = xy(f.GetPosition())
        bx = box(f)
        w, h = bx[2] - bx[0], bx[3] - bx[1]
        best = None
        for r in [k * 0.25 for k in range(1, 60)]:
            for k in range(24):
                a = 2 * math.pi * k / 24
                x, y = x0 + r * math.cos(a), y0 + r * math.sin(a)
                cand = (x - (x0 - bx[0]), y - (y0 - bx[1]), x - (x0 - bx[0]) + w, y - (y0 - bx[1]) + h)
                if hit(cand, corr, 0.3) or any(hit(cand, o, 0.25) for o in others):
                    continue
                best = (x, y)
                break
            if best:
                break
        if best:
            f.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(best[0]), pcbnew.FromMM(best[1])))
            moved.append(f.GetReference())
    return moved


def preroute_vdda33(b, reach=1.2, width=0.2):
    """Hub pin 5 (VDDA33) sits between USB ports 2 and 3. Give it a locked straight escape stub
    out between the two pairs' exits; the pairs route around its end and the autorouter joins
    it to the VDDA33 decoupling from open space afterwards."""
    f = next(f for f in b.GetFootprints() if f.GetReference() == 'U5')
    p5 = next(p for p in f.Pads() if p.GetNumber() == '5')
    a = xy(p5.GetPosition())
    c = xy(f.GetPosition())
    out = unit((a[0] - c[0], 0) if abs(a[0] - c[0]) > abs(a[1] - c[1]) else (0, a[1] - c[1]))
    half = max(pcbnew.ToMM(p5.GetSize().x), pcbnew.ToMM(p5.GetSize().y)) / 2
    end = (a[0] + out[0] * (half + reach), a[1] + out[1] * (half + reach))
    _add_trk(b, p5.GetNetname(), a, end, pcbnew.F_Cu)
    return True


def _hybrid_runs(grid, gF, gB, vblk, htip, hout, ftip, fout, lead=0.8):
    """F.Cu stub straight out from each chip to the nearest via site where both transition vias
    clear everything, then a coupled B.Cu run between the sites (straight for `lead` mm past
    each via before it may turn)."""
    def free(blk, p):
        i, j = grid.cell(*p)
        return 0 <= i < blk.shape[0] and 0 <= j < blk.shape[1] and not blk[i, j]

    def site(tip, out):
        lat = (-out[1], out[0])
        for k in range(8, 60):                       # 0.8 .. 6 mm out
            d = k * 0.1
            cpt = (tip[0] + out[0] * d, tip[1] + out[1] * d)
            vias = [(cpt[0] + lat[0] * VIA_OFF * sg, cpt[1] + lat[1] * VIA_OFF * sg) for sg in (1, -1)]
            stub_ok = all(free(gF, (tip[0] + out[0] * t * 0.1, tip[1] + out[1] * t * 0.1)) for t in range(4, k + 1))
            lead_pt = (cpt[0] + out[0] * lead, cpt[1] + out[1] * lead)
            if stub_ok and all(free(vblk, v) for v in vias) and free(gB, cpt) and free(gB, lead_pt):
                return cpt, lead_pt
        return None
    hs, fs = site(htip, hout), site(ftip, fout)
    if os.environ.get('USB_DEBUG'):
        print(f'      hybrid: hub via site {"found" if hs else "NONE"}, FT via site {"found" if fs else "NONE"}')
    if not hs or not fs:
        return None
    (hc, hl), (fc, fl) = hs, fs
    mid = _astar(grid, gB, hl, fl)
    if os.environ.get('USB_DEBUG'):
        print(f'      hybrid: B.Cu path {"found" if mid else "NONE"} from {tuple(round(v - 30, 1) for v in hl)} to {tuple(round(v - 30, 1) for v in fl)}')
    if not mid:
        return None
    return [(pcbnew.F_Cu, [htip, hc]), (pcbnew.B_Cu, [hc] + mid + [fc]), (pcbnew.F_Cu, [fc, ftip])]


def route_port_2layer(b, k, band, noisy):
    dp, dm = f'USB_DN{k}_DP', f'USB_DN{k}_DM'
    hpads, hpts, hmid, hout, htip = pin_pair(b, 'U5', HUB[k])
    hpads[0].SetNet(b.FindNet(dp))
    hpads[1].SetNet(b.FindNet(dm))
    fpads, fpts, fmid, fout, ftip = pin_pair(b, f'U{k}', FT_PINS)
    start = (htip[0] + hout[0] * ESCAPE, htip[1] + hout[1] * ESCAPE)
    goal = (ftip[0] + fout[0] * ESCAPE, ftip[1] + fout[1] * ESCAPE)
    from close_gaps import BOARD_BOX
    mg = float(os.environ.get('USB_MARGIN', '30'))
    x1, y1 = max(min(start[0], goal[0]) - mg, BOARD_BOX[0]), max(min(start[1], goal[1]) - mg, BOARD_BOX[1])
    x2, y2 = min(max(start[0], goal[0]) + mg, BOARD_BOX[2]), min(max(start[1], goal[1]) + mg, BOARD_BOX[3])
    grid = maze.Grid(b, {dp, dm}, (x1, y1, x2, y2), 2 * (HALF + W / 2), CLR + 0.05, VIA_D)   # +0.05: grid rounding
    gF, gB = grid.block[pcbnew.F_Cu], grid.block[pcbnew.B_Cu]
    # via sites must clear EVERY pad and track, the pair's own included (a D+ via can't sit on a D- pad)
    vgrid = maze.Grid(b, set(), (x1, y1, x2, y2), 2 * (HALF + W / 2), CLR + 0.05, VIA_D)
    vblk = vgrid.vblock
    # 24 V copper needs 0.3 mm, not 0.15: grow it by the difference
    hv = lambda n: n.startswith('+24V') or n.endswith('/24V_FUSED') or n.endswith('/SW3') or n.endswith('/SW5')
    for f in b.GetFootprints():
        for p in f.Pads():
            if hv(p.GetNetname()):
                bb = p.GetBoundingBox()
                bx = (pcbnew.ToMM(bb.GetLeft()), pcbnew.ToMM(bb.GetTop()), pcbnew.ToMM(bb.GetRight()),
                      pcbnew.ToMM(bb.GetBottom()))
                for lay, g_ in ((pcbnew.F_Cu, gF), (pcbnew.B_Cu, gB)):
                    if p.IsOnLayer(lay):
                        grid._rect(g_, bx, HALF + W / 2 + 0.32)
    for f in b.GetFootprints():          # bucks' noisy parts: keep the pair off them on both layers
        if any(p.GetNetname() in noisy for p in f.Pads()):
            bb = f.GetBoundingBox(False)
            box = (pcbnew.ToMM(bb.GetLeft()), pcbnew.ToMM(bb.GetTop()), pcbnew.ToMM(bb.GetRight()),
                   pcbnew.ToMM(bb.GetBottom()))
            grid._rect(gF, box, 1.0)
            grid._rect(gB, box, 0.5)
    # existing F.Cu power pours (buck inputs/outputs, switch nodes) are off limits for the pair
    for z in b.Zones():
        try:
            nm = z.GetZoneName()
        except Exception:
            continue
        if z.GetLayer() == pcbnew.F_Cu and nm and not nm.startswith('GND') and not z.GetIsRuleArea():
            pts = [xy(z.GetCornerPosition(i)) for i in range(z.GetNumCorners())]
            if pts:
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                grid._rect(gF, (min(xs), min(ys), max(xs), max(ys)), HALF + W / 2 + 0.32)
    # no B.Cu run (and so no In2 keepout) under the hub: its other pin rows escape through In2/B.Cu
    # vias right there. Only the side the pair leaves from stays open.
    m = float(os.environ.get('USB_HUB_KEEP', '1.2'))
    for ref, out in (('U5', hout), (f'U{k}', fout)):     # same for the FT4232H the pair lands on
        hb = b.FindFootprintByReference(ref).GetBoundingBox(False)
        l, t, r, btm = (pcbnew.ToMM(hb.GetLeft()), pcbnew.ToMM(hb.GetTop()), pcbnew.ToMM(hb.GetRight()),
                        pcbnew.ToMM(hb.GetBottom()))
        box = (l - (0 if out[0] < -0.5 else m), t - (0 if out[1] < -0.5 else m),
               r + (0 if out[0] > 0.5 else m), btm + (0 if out[1] > 0.5 else m))
        if m > 0:
            grid._rect(gB, box, HALF + W / 2)
    # B.Cu runs may cross the +5V band: where they do, the band (priority 5) keeps its copper and is
    # the pair's reference there; elsewhere the GND strip (priority 4) beats the 3.3 V pour (0)
    for p in (start, goal):
        i, j = grid.cell(*p)
        r = int(0.45 / maze.RES)
        gF[max(i - r, 0):i + r + 1, max(j - r, 0):j + r + 1] = False
    path = None
    if os.environ.get('USB_FCU_FIRST', '1') == '1':
        # try F.Cu only first (no vias, directly over the In1 plane): fast single-layer search
        c = _astar(grid, gF, start, goal)
        if c:
            path = 'F'
            runs = [(pcbnew.F_Cu, c)]
    if path is None and os.environ.get('USB_NO_VIAS') == '1':
        return None
    if path is None:
        runs = _hybrid_runs(grid, gF, gB, vblk, htip, hout, ftip, fout)
        if not runs:
            return None
        path = 'FBF'
        # square-on lead-in/out: run straight from each pin row to the search start/goal
    if path != 'FBF':
        runs[0] = (runs[0][0], [htip] + runs[0][1])
        runs[-1] = (runs[-1][0], runs[-1][1] + [ftip])
    # transition vias: one position per line per layer change, shared by both runs
    trans = []
    for ri in range(len(runs) - 1):
        c = runs[ri][1]
        p = c[-1]
        prev = c[-2] if len(c) > 1 else runs[ri - 1][1][-1] if ri > 0 else start
        d = unit((p[0] - prev[0], p[1] - prev[1]))
        trans.append({'L': (p[0] + d[1] * VIA_OFF, p[1] - d[0] * VIA_OFF),      # left of travel
                      'R': (p[0] - d[1] * VIA_OFF, p[1] + d[0] * VIA_OFF)})
    sides = {}
    pieces = {'L': [], 'R': []}
    for ri, (layer, c) in enumerate(runs):
        if len(c) < 2:
            c = c + c
        left, right = offset(c, HALF), offset(c, -HALF)
        for key, line in (('L', left), ('R', right)):
            pts = list(line)
            if ri > 0:
                pts[0] = trans[ri - 1][key]
            if ri < len(runs) - 1:
                pts[-1] = trans[ri][key]
            pieces[key].append((layer, pts))
    # which line is D+? match pins to line ends without crossing (order across the pin row)
    def assign(end_pts, pin_pts, mid, out):
        lat = (-out[1], out[0])
        proj = lambda q: (q[0] - mid[0]) * lat[0] + (q[1] - mid[1]) * lat[1]
        lines_sorted = sorted(('L', 'R'), key=lambda k_: proj(end_pts[k_]))
        pins_sorted = sorted((0, 1), key=lambda i: proj(pin_pts[i]))
        return {lines_sorted[0]: pins_sorted[0], lines_sorted[1]: pins_sorted[1]}
    at_hub = assign({k_: pieces[k_][0][1][0] for k_ in 'LR'}, hpts, hmid, hout)
    at_ft = assign({k_: pieces[k_][-1][1][-1] for k_ in 'LR'}, fpts, fmid, fout)
    if at_hub != at_ft:
        raise RuntimeError(f'port {k}: D+/D- arrive mirrored at FT{k} (hub swap not in effect?)')
    lens = {}
    for key in 'LR':
        i = at_hub[key]                  # 0 = D+, 1 = D-
        net = dp if i == 0 else dm
        total = 0.0
        segs = pieces[key]
        for n_, (layer, pts) in enumerate(segs):
            if n_ == 0:
                pts = [hpts[i]] + pts
            if n_ == len(segs) - 1:
                pts = pts + [fpts[i]]
            for a_, cc in zip(pts, pts[1:]):
                _add_trk(b, net, a_, cc, layer)
            total += poly_len(pts)
            if n_ < len(segs) - 1:
                _add_via(b, net, pts[-1])
        lens[net] = total
    # In2 GND reference strip + routing keepout over every B.Cu run
    b_len = 0.0
    for layer, c in runs:
        if layer != pcbnew.B_Cu or len(c) < 2:
            continue
        b_len += poly_len(c)
        # extend the run a little at both ends, then outline it at +/- STRIP_HALF
        d0 = unit((c[1][0] - c[0][0], c[1][1] - c[0][1]))
        d1 = unit((c[-1][0] - c[-2][0], c[-1][1] - c[-2][1]))
        cc = [(c[0][0] - d0[0] * STRIP_HALF, c[0][1] - d0[1] * STRIP_HALF)] + c[1:-1] + \
             [(c[-1][0] + d1[0] * STRIP_HALF, c[-1][1] + d1[1] * STRIP_HALF)]
        outline = offset(cc, STRIP_HALF) + list(reversed(offset(cc, -STRIP_HALF)))
        _zone(b, pcbnew.In2_Cu, 'GND', outline, k, name=f'USB{k}_REF')    # 1..4: distinct, all below the +5V band (5)
        _zone(b, pcbnew.In2_Cu, '', outline, 0, rule_area=True, name=f'USB{k}_KEEPOUT')
    n_vias = sum(1 for _ in runs) - 1
    return lens, n_vias, b_len, [c for layer, c in runs if layer == pcbnew.B_Cu]


def route_all_2layer(b):
    """VDDA33 escape first, then the four pairs (trying orders), all locked. Report lines or None."""
    import itertools
    noisy = {'/Power/' + n for n in ('SW5', 'SW3', 'BOOT5', 'BOOT3', 'FB5', 'FB3', 'COMP5', 'COMP5_RC',
                                      'COMP3', 'COMP3_RC', 'RT5', 'RT3')}
    moved = clear_hub_corridor(b)
    if not preroute_vdda33(b):
        return None
    band = _band_capsule(b)
    base = {t.m_Uuid.AsString() for t in b.GetTracks()}
    # 4-2-3-1 keeps ports 1 and 4 entirely on F.Cu and port 3 almost so (found by trying orders)
    for order in [tuple(int(c) for c in os.environ.get('USB_ORDER', '4231'))]:
        report, ok, b_runs = [], True, []
        for k in order:
            r = route_port_2layer(b, k, band, noisy)
            if not r:
                ok = False
                report.append(f'port {k} failed')
                break
            lens, n_tr, b_len, bruns = r
            b_runs += bruns
            lp, lm = lens[f'USB_DN{k}_DP'], lens[f'USB_DN{k}_DM']
            report.append(f'port {k}: D+ {lp:.1f} mm, D- {lm:.1f} mm, mismatch {abs(lp - lm):.2f} mm, '
                          f'{n_tr} layer change(s), {b_len:.0f} mm on B.Cu over an In2 GND strip')
        new = [t for t in b.GetTracks() if t.m_Uuid.AsString() not in base]
        if ok:
            for t in new:
                t.SetLocked(True)
            n_gnd = _stitch_beside(b, b_runs)
            return ([f'moved out of the hub USB corridor: {", ".join(moved) or "nothing"}', 'order ' + ''.join(map(str, order))]
                    + report + [f'{n_gnd} GND vias stitched beside B.Cu runs'])
        print('   tried', ''.join(map(str, order)), '-', report[-1])
        return None    # zones were added for B.Cu runs; retrying orders in-process is unsafe (no zone removal)
    return None


def main():
    order = [int(ch) for ch in (sys.argv[1] if len(sys.argv) > 1 else '1234')]
    nets = [f'USB_DN{k}_{s}' for k in order for s in ('DP', 'DM')]
    n = strip_tracks(PCB, nets)
    print(f'ripped {n} tracks/vias of the 8 downstream USB nets')
    b = pcbnew.LoadBoard(PCB)
    ok = True
    for k in order:
        r = route_port(b, k)
        if not r:
            print(f'port {k}: no F.Cu path for the coupled pair')
            ok = False
            continue
        lens, corners = r
        lp, lm = lens[f'USB_DN{k}_DP'], lens[f'USB_DN{k}_DM']
        print(f'port {k}: D+ {lp:.1f} mm, D- {lm:.1f} mm, mismatch {abs(lp - lm):.2f} mm, {corners - 2} bends, 0 vias')
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    pcbnew.SaveBoard(PCB, b)
    print('saved' if ok else 'saved (incomplete)')


if __name__ == '__main__':
    main()
