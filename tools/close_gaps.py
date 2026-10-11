"""Close the connections the autorouter left open, using KiCad's own DRC report.

* Ground pads/fragments with no path to the plane get a via with a short stub,
  placed only where it clears every other net's copper.
* "Near misses" (a routed track that ends next to its pad or track but does not touch)
  get a short joining segment on a shared layer, after a clearance check.
* Ground-pour islands get a via inside the island.

Run after route.py:  python3 tools/close_gaps.py
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
PCB = os.path.join(PROJ, 'usb-console-server.kicad_pcb')
sys.path.insert(0, HERE)
from route import _seg_dist, _box_dist  # noqa: E402

CU = [pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.B_Cu]
POUR_NETS = {'+3.3V', 'GND'}   # nets with an inner-layer pour (In2 / In1)
MAZE_TODO = set()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_pcb import OX, OY, W, H  # noqa: E402
BOARD_BOX = (OX + 0.5, OY + 0.5, OX + W - 0.5, OY + H - 0.5)


def mm(v):
    return pcbnew.FromMM(v)


def xy(v):
    return pcbnew.ToMM(v.x), pcbnew.ToMM(v.y)


def drc_unconnected():
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, 'd.json')
        subprocess.run(['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-error', '-o', out, PCB],
                       capture_output=True)
        d = json.load(open(out))
    return d['unconnected_items'], [v for v in d['violations']]


def net_clearance(net):
    """Required clearance to copper of `net` (mirrors the project's net classes)."""
    if net.startswith('+24V') or net.endswith('/24V_FUSED') or net.endswith('/SW3'):
        return 0.3
    if net in ('+5V', 'PI_5V') or net.endswith('/SW5'):
        return 0.25
    return 0.15


class Copper:
    """Every copper object as simple geometry, for clearance checks."""
    def __init__(self, b):
        self.pads, self.segs = [], []
        for f in b.GetFootprints():
            for p in f.Pads():
                bb = p.GetBoundingBox()
                layers = [l for l in CU if p.IsOnLayer(l)]
                self.pads.append((p.GetNetname(), layers, (pcbnew.ToMM(bb.GetLeft()), pcbnew.ToMM(bb.GetTop()),
                                                          pcbnew.ToMM(bb.GetRight()), pcbnew.ToMM(bb.GetBottom()))))
        for t in b.GetTracks():
            if t.Type() == pcbnew.PCB_VIA_T:
                x, y = xy(t.GetPosition())
                self.segs.append((t.GetNetname(), CU, (x, y, x, y), pcbnew.ToMM(t.GetWidth(pcbnew.F_Cu)) / 2, t))
            else:
                (x1, y1), (x2, y2) = xy(t.GetStart()), xy(t.GetEnd())
                self.segs.append((t.GetNetname(), [t.GetLayer()], (x1, y1, x2, y2), pcbnew.ToMM(t.GetWidth()) / 2, t))

    def point_ok(self, x, y, r, net, layers, gap, rip=None):
        """rip: a set to collect blocking TRACKS of pour nets instead of failing on them."""
        for n, ls, box in self.pads:
            if n != net and set(ls) & set(layers) and _box_dist(x, y, box) < r + max(gap, net_clearance(n)):
                return False
        for n, ls, s, hw, obj in self.segs:
            if n != net and set(ls) & set(layers) and _seg_dist(x, y, *s) < r + hw + max(gap, net_clearance(n)):
                if rip is not None and n in POUR_NETS and obj is not None and obj.Type() == pcbnew.PCB_TRACE_T:
                    rip[obj.m_Uuid.AsString()] = obj
                    continue
                return False
        return True

    def segment_ok(self, a, c, hw, net, layer, gap=0.15, rip=None):
        n = max(2, int(math.dist(a, c) / 0.1))
        return all(self.point_ok(a[0] + (c[0] - a[0]) * i / n, a[1] + (c[1] - a[1]) * i / n, hw, net, [layer], gap,
                                 rip) for i in range(n + 1))

    def add_seg(self, net, layers, s, hw, obj=None):
        self.segs.append((net, layers, s, hw, obj))

    def remove(self, b, objs):
        gone = set(objs)
        for o in objs.values():
            b.Remove(o)
        self.segs = [s for s in self.segs if s[4] is None or s[4].m_Uuid.AsString() not in gone]


def add_track(b, cu, net, a, c, layer, width):
    t = pcbnew.PCB_TRACK(b)
    t.SetStart(pcbnew.VECTOR2I(mm(a[0]), mm(a[1])))
    t.SetEnd(pcbnew.VECTOR2I(mm(c[0]), mm(c[1])))
    t.SetLayer(layer)
    t.SetWidth(mm(width))
    t.SetNet(b.FindNet(net))
    b.Add(t)
    cu.add_seg(net, [layer], (*a, *c), width / 2, t)


def add_via(b, cu, net, p, dia=0.6, drill=0.3):
    v = pcbnew.PCB_VIA(b)
    v.SetPosition(pcbnew.VECTOR2I(mm(p[0]), mm(p[1])))
    v.SetWidth(mm(dia))
    v.SetDrill(mm(drill))
    v.SetNet(b.FindNet(net))
    b.Add(v)
    cu.add_seg(net, CU, (*p, *p), dia / 2, v)


def via_stub(b, cu, net, origin, layer, width=0.25, rmax=5.0):
    """A via near `origin` on `net`, joined to it by a short track on `layer`."""
    for dia, drill, w in ((0.6, 0.3, width), (0.5, 0.25, 0.2), (0.5, 0.25, 0.15)):
        for r in [x * 0.25 for x in range(3, int(rmax / 0.25) + 1)]:
            for k in range(32):
                a = 2 * math.pi * k / 32
                p = (origin[0] + r * math.cos(a), origin[1] + r * math.sin(a))
                if cu.point_ok(*p, dia / 2, net, CU, 0.2) and cu.segment_ok(origin, p, w / 2, net, layer):
                    add_track(b, cu, net, origin, p, layer, w)
                    add_via(b, cu, net, p, dia, drill)
                    return p
    # boxed in: rip up blocking +3.3V / GND tracks (they re-drop onto their pours next pass)
    best = None
    for r in [x * 0.25 for x in range(3, int(rmax / 0.25) + 1)]:
        for k in range(32):
            a = 2 * math.pi * k / 32
            p = (origin[0] + r * math.cos(a), origin[1] + r * math.sin(a))
            rip = {}
            if cu.point_ok(*p, 0.25, net, CU, 0.2, rip) and cu.segment_ok(origin, p, 0.075, net, layer, rip=rip):
                if best is None or len(rip) < len(best[1]):
                    best = (p, rip)
        if best and r > 1.5:
            break
    if best:
        p, rip = best
        cu.remove(b, rip)
        add_track(b, cu, net, origin, p, layer, 0.15)
        add_via(b, cu, net, p, 0.5, 0.25)
        print(f'    {net}: ripped {len(rip)} blocking pour-net segment(s) to reach a via')
        return p
    return None


def via_bridge(b, cu, net, a, c, width):
    """Join a and c through a pair of vias and a segment on another layer."""
    if not (cu.point_ok(*a, 0.25, net, CU, 0.2) and cu.point_ok(*c, 0.25, net, CU, 0.2)):
        return False
    for layer in (pcbnew.B_Cu, pcbnew.In2_Cu, pcbnew.F_Cu):
        if cu.segment_ok(a, c, width / 2, net, layer):
            add_via(b, cu, net, a, 0.5, 0.25)
            add_via(b, cu, net, c, 0.5, 0.25)
            add_track(b, cu, net, a, c, layer, width)
            return True
    return False


def path_options(a, c):
    """Straight, two L-shapes and two 45-degree dog-legs from a to c."""
    dx, dy = c[0] - a[0], c[1] - a[1]
    d = min(abs(dx), abs(dy))
    sx, sy = (1 if dx >= 0 else -1), (1 if dy >= 0 else -1)
    yield [a, c]
    yield [a, (a[0], c[1]), c]
    yield [a, (c[0], a[1]), c]
    yield [a, (a[0] + sx * d, a[1] + sy * d), c]
    yield [a, (c[0] - sx * d, c[1] - sy * d), c]


def connect_path(b, cu, net, a, c, layer, width):
    for pts in path_options(a, c):
        if all(cu.segment_ok(p, q, width / 2, net, layer) for p, q in zip(pts, pts[1:]) if math.dist(p, q) > 1e-6):
            for p, q in zip(pts, pts[1:]):
                if math.dist(p, q) > 1e-6:
                    add_track(b, cu, net, p, q, layer, width)
            return True
    return False


def same_net_targets(b, cu, net, origin, exclude_pts, radius=6.0):
    """Points on same-net copper near origin: vias, pads, and samples along tracks."""
    out = []
    for n, ls, s, hw, o in cu.segs:
        if n != net:
            continue
        x1, y1, x2, y2 = s
        k = max(1, int(math.dist((x1, y1), (x2, y2)) / 0.25))
        for i in range(k + 1):
            p = (x1 + (x2 - x1) * i / k, y1 + (y2 - y1) * i / k)
            if math.dist(p, origin) <= radius:
                out.append((p, ls))
    for n, ls, box in cu.pads:
        if n == net:
            p = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
            if math.dist(p, origin) <= radius:
                out.append((p, ls))
    out = [(p, ls) for p, ls in out if all(math.dist(p, e) > 0.6 for e in exclude_pts)]
    return sorted(out, key=lambda t: math.dist(t[0], origin))


def reach_same_net(b, cu, net, origin, layer, width, exclude_pts):
    """Connect origin to the nearest reachable same-net copper not in its own fragment."""
    for p, ls in same_net_targets(b, cu, net, origin, exclude_pts)[:80]:
        if layer in ls and connect_path(b, cu, net, origin, p, layer, width):
            return True
    return False


def reach_vias(b, cu, origin, layer, vias, width=0.15):
    for v in sorted(vias, key=lambda v: math.dist(v, origin))[:30]:
        if math.dist(v, origin) > 6:
            break
        if connect_path(b, cu, 'GND', origin, v, layer, width):
            return True
    return False


def item_points(it):
    """Connection points and copper layers of a reported item."""
    if it.Type() == pcbnew.PCB_TRACE_T or it.Type() == pcbnew.PCB_ARC_T:
        return [xy(it.GetStart()), xy(it.GetEnd())], [it.GetLayer()]
    if it.Type() == pcbnew.PCB_VIA_T:
        return [xy(it.GetPosition())], CU
    if it.Type() == pcbnew.PCB_PAD_T:
        return [xy(it.GetPosition())], [l for l in CU if it.IsOnLayer(l)]
    return [], []


def net_pads(b, net):
    return [p for f in b.GetFootprints() for p in f.Pads() if p.GetNetname() == net]


def strip_tracks(path, nets):
    """Delete the unlocked tracks and vias of `nets` from the saved board file.
    (Removing vias through the Python binding invalidates other wrappers.)"""
    want = {f'(net "{n}")' for n in nets}
    out, block, n_removed = [], None, 0
    for line in open(path).read().split('\n'):
        if block is None and line in ('\t(segment', '\t(via', '\t(arc'):
            block = [line]
            continue
        if block is not None:
            block.append(line)
            if line == '\t)':
                text = '\n'.join(block)
                if any(w in text for w in want) and '(locked yes)' not in text:
                    n_removed += 1
                else:
                    out.extend(block)
                block = None
            continue
        out.append(line)
    open(path, 'w').write('\n'.join(out))
    return n_removed


def strip_uuids(path, uuids):
    """Delete specific tracks/vias (by uuid) from the saved board file."""
    want = {f'(uuid "{u}")' for u in uuids}
    out, block, n = [], None, 0
    for line in open(path).read().split('\n'):
        if block is None and line in ('\t(segment', '\t(via', '\t(arc'):
            block = [line]
            continue
        if block is not None:
            block.append(line)
            if line == '\t)':
                text = '\n'.join(block)
                if any(w in text for w in want):
                    n += 1
                else:
                    out.extend(block)
                block = None
            continue
        out.append(line)
    open(path, 'w').write('\n'.join(out))
    return n


def drop_violating_gnd_vias():
    """Phase 0: ground vias added by stitching that violate clearance are simply removed."""
    _, viol = drc_unconnected()
    bad = []
    for v in viol:
        if v['type'] not in ('clearance', 'hole_clearance', 'copper_edge_clearance'):
            continue
        for it in v['items']:
            if it['description'].startswith('Via [GND]'):
                bad.append(it['uuid'])
    # also every same-net via stacked at those spots, and duplicate stacked vias anywhere
    import re as _re
    text = open(PCB).read()
    vias = _re.findall(r'\t\(via\n\t\t\(at ([\d.\-]+) ([\d.\-]+)\)\n(?:\t\t[^\n]*\n)*?\t\t\(net "([^"]*)"\)\n\t\t\(uuid "([^"]+)"\)', text)
    at = {u: (x, y, n) for x, y, n, u in vias}
    bad_spots = {at[u] for u in bad if u in at}
    seen = set()
    for x, y, n, u in vias:
        key = (x, y, n)
        if key in bad_spots:
            bad.append(u)
        elif key in seen:
            bad.append(u)          # exact duplicate of a via already kept
        seen.add(key)
    segs = _re.findall(r'\t\(segment\n\t\t\(start ([^)]*)\)\n\t\t\(end ([^)]*)\)\n\t\t\(width ([^)]*)\)\n'
                       r'\t\t\(layer "([^"]*)"\)\n\t\t\(net "([^"]*)"\)\n\t\t\(uuid "([^"]+)"\)', text)
    seen_s = set()
    for st, en, w, ly, n_, u in segs:
        k = (tuple(sorted((st, en))), ly, n_)
        if k in seen_s:
            bad.append(u)
        seen_s.add(k)
    if bad:
        n = strip_uuids(PCB, set(bad))
        b = pcbnew.LoadBoard(PCB)
        pcbnew.ZONE_FILLER(b).Fill(b.Zones())
        pcbnew.SaveBoard(PCB, b)
        print(f'removed {n} via(s): clearance violators and stacked duplicates')


def maze_route_pads(b, net, width):
    """Route a two-pad net pad to pad with the maze router."""
    import maze
    pads = net_pads(b, net)
    if len(pads) != 2:
        return False
    (a, la), (c, lc) = [(xy(p.GetPosition()), [l for l in maze.LAYERS if p.IsOnLayer(l)]) for p in pads]
    res = maze.route(b, net, a, c, la, lc, width=width, clearance=max(0.15, net_clearance(net)),
                     margin=12, board_box=BOARD_BOX)
    if not res:
        return False
    maze.commit(b, net, res, width=width, start=a, goal=c, start_layers=la, goal_layers=lc)
    return True


def tree_route(b, net, width):
    """Route every pad of a (ripped) net, each to the nearest copper already on the net."""
    import maze
    pads = net_pads(b, net)
    if not pads:
        return True
    pts = [(xy(p.GetPosition()), [l for l in maze.LAYERS if p.IsOnLayer(l)]) for p in pads]
    tree = [pts[0]]
    rest = pts[1:]
    while rest:
        rest.sort(key=lambda q: min(math.dist(q[0], t[0]) for t in tree))
        p, lp = rest.pop(0)
        near = sorted(tree, key=lambda t: math.dist(p, t[0]))
        goal, gl = near[0]
        res = maze.route(b, net, p, goal, lp, gl, width=width, clearance=max(0.15, net_clearance(net)),
                         margin=6, board_box=BOARD_BOX, extra_goals=near[1:12])
        if not res:
            return False
        maze.commit(b, net, res, width=width, start=p, start_layers=lp)
        runs, vias = res
        for layer, rpts in runs:
            tree += [(q, [layer]) for q in rpts]
        tree += [(v, maze.LAYERS) for v in vias]
        tree.append((p, lp))
    return True


def maze_attempt(group, width, walls=()):
    """One process, one attempt: strip `group` (+ wall nets), load once, route the group
    pad to pad, rebuild the wall nets as trees, save. (Reloading boards in one process is unstable.)"""
    strip_tracks(PCB, list(group) + list(walls))
    b = pcbnew.LoadBoard(PCB)
    order = sorted(group, key=lambda n: not n.endswith('_DP'))
    for n in order:
        ok = maze_route_pads(b, n, width) if len(net_pads(b, n)) == 2 else tree_route(b, n, min(width, 0.15))
        if not ok:
            return False
    for w in sorted(walls, key=lambda n: (len(net_pads(b, n)), n)):
        if not tree_route(b, w, 0.16 if 'USB_' in w else 0.15):
            print(f'    wall net {w} could not be rebuilt')
            return False
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    pcbnew.SaveBoard(PCB, b)
    return True


def find_walls(group):
    import maze
    b = pcbnew.LoadBoard(PCB)
    walls = set()
    for net in group:
        for p in net_pads(b, net):
            walls |= maze.sealed_walls(b, net, xy(p.GetPosition()), [l for l in maze.LAYERS if p.IsOnLayer(l)])
    return sorted(walls - set(group) - POUR_NETS)


def maze_phase(nets):
    """Rip each listed two-pad net (then its USB pair partner, then the nets that wall its
    pins in) and maze-route it, each attempt in a fresh process."""
    import shutil
    for net in sorted(nets):
        partner = net[:-1] + ('M' if net.endswith('P') else 'P') if net[-3:] in ('_DP', '_DM') else None
        ok = False
        groups = [[net]] + ([[net, partner]] if partner else [])
        base = groups[-1]
        # which nets wall the pins in? try ripping them one at a time, then all together
        r = subprocess.run([sys.executable, os.path.abspath(__file__), '--walls-of', *base],
                           capture_output=True, text=True)
        walls = [w for w in r.stdout.strip().split('|') if w]
        if walls:
            print(f'    {net} is sealed in by: {", ".join(walls)}')
            groups += [base + ['--rip', w] for w in walls] + ([base + ['--rip', *walls]] if len(walls) > 1 else [])
        for group in groups:
            shutil.copy(PCB, PCB + '.bak')
            r = subprocess.run([sys.executable, os.path.abspath(__file__), '--maze', *group],
                               capture_output=True, text=True)
            for line in r.stdout.splitlines():
                if line.startswith('    '):
                    print(line)
            if 'MAZE_OK' in r.stdout:
                nets_ = group[:group.index('--rip')] if '--rip' in group else group
                rip_ = group[group.index('--rip') + 1:] if '--rip' in group else []
                print(f'    {"/".join(nets_)}: ripped and maze-routed'
                      + (f' (rebuilt {", ".join(rip_)} around it)' if rip_ else ''))
                ok = True
                break
            shutil.copy(PCB + '.bak', PCB)
        if not ok:
            print(f'    {net}: maze router found no path')


def fix_pass(b):

    unconnected, _ = drc_unconnected()
    cu = Copper(b)
    index = {}
    for t in b.GetTracks():
        index[t.m_Uuid.AsString()] = t
    for f in b.GetFootprints():
        for p in f.Pads():
            index[p.m_Uuid.AsString()] = p
    for z in b.Zones():
        index[z.m_Uuid.AsString()] = z
    done, failed = 0, []
    for u in unconnected:
        items = []
        for i in u['items']:
            items.append(index.get(i['uuid']))
        net = items[0].GetNetname() if items[0] else ''
        if any(it is None for it in items):
            failed.append(u['items'][0]['description'])
            continue
        zones = [it for it in items if it.Type() == pcbnew.PCB_ZONE_T]
        others = [it for it in items if it.Type() != pcbnew.PCB_ZONE_T]
        if net == 'GND':
            # every stranded ground piece gets its own via to the In1 plane
            ok = True
            for it in others:
                pts, layers = item_points(it)
                if pcbnew.In1_Cu in layers:
                    continue
                layer = layers[0]
                if not any(via_stub(b, cu, net, p, layer) for p in pts):
                    vias_gnd = [s_[2][:2] for s_ in cu.segs if s_[0] == 'GND' and s_[4] is not None
                                and s_[4].Type() == pcbnew.PCB_VIA_T]
                    if not any(reach_vias(b, cu, p, layer, vias_gnd) for p in pts):
                        ok = False
            if zones and not others:
                ok = island_vias(b, cu, zones[0]) and ok
            done += ok
            if not ok:
                failed.append(u['items'][0]['description'])
            continue
        # near miss: join the closest pair of points
        (pa, la), (pb, lb) = (item_points(items[0]), item_points(items[1]))
        widths = [pcbnew.ToMM(it.GetWidth()) for it in items if it.Type() == pcbnew.PCB_TRACE_T]
        width = max(0.127, min(max(widths) if widths else 0.25, 0.6))   # match the routed track (fab min 0.127)
        def along(it, pts):
            if it.Type() != pcbnew.PCB_TRACE_T:
                return pts
            (x1, y1), (x2, y2) = pts
            n = max(1, int(math.dist(pts[0], pts[1]) / 0.25))
            return [(x1 + (x2 - x1) * i / n, y1 + (y2 - y1) * i / n) for i in range(n + 1)]
        pa_all, pb_all = along(items[0], pa), along(items[1], pb)
        pairs = sorted(((math.dist(a, c), a, c) for a in pa for c in pb), key=lambda t: t[0])
        bridge_pairs = sorted(((math.dist(a, c), a, c) for a in pa_all for c in pb_all), key=lambda t: t[0])
        closed = False
        for d, a, c in pairs[:4]:
            if d > 12:
                break
            for layer in set(la) & set(lb) - {pcbnew.In1_Cu}:
                if cu.segment_ok(a, c, width / 2, net, layer):
                    add_track(b, cu, net, a, c, layer, width)
                    closed = True
                    break
            if closed:
                break
            if not set(la) & set(lb):
                # different layers: a via at one end, then a segment on the other item's layer
                for (p, q, lq) in ((a, c, lb[0]), (c, a, la[0])):
                    if cu.point_ok(*p, 0.25, net, CU, 0.2) and cu.segment_ok(p, q, width / 2, net, lq):
                        add_via(b, cu, net, p, 0.5, 0.25)
                        add_track(b, cu, net, p, q, lq, width)
                        closed = True
                        break
            if closed:
                break
        if not closed:
            for d, a, c in bridge_pairs[:60]:
                if via_bridge(b, cu, net, a, c, min(width, 0.3)):
                    closed = True
                    break
        if not closed:
            own = pa_all
            for start in pb_all[:1] + pa_all[:1]:
                pass
            for it_, pts_ in ((items[0], pa_all), (items[1], pb_all)):
                pts0, lays0 = item_points(it_)
                other_item = items[1] if it_ is items[0] else items[0]
                other_lays = item_points(other_item)[1]
                other = pb_all if pts_ is pa_all else pa_all
                for o in pts0:
                    for t in sorted(other, key=lambda q: math.dist(q, o))[:20]:
                        # only on a layer both pieces are really on
                        for layer in set(lays0) & set(other_lays) - {pcbnew.In1_Cu}:
                            if connect_path(b, cu, net, o, t, layer, min(width, 0.3)):
                                closed = True
                                break
                        if closed:
                            break
                        # no shared layer: a via at this end, then a path on the other piece's layer
                        if not set(lays0) & set(other_lays) and cu.point_ok(*o, 0.25, net, CU, 0.2):
                            for layer in set(other_lays) - {pcbnew.In1_Cu}:
                                if connect_path(b, cu, net, o, t, layer, min(width, 0.2)):
                                    add_via(b, cu, net, o, 0.5, 0.25)
                                    closed = True
                                    break
                        if closed:
                            break
                        # through another layer: vias at both ends
                        if cu.point_ok(*o, 0.25, net, CU, 0.2) and cu.point_ok(*t, 0.25, net, CU, 0.2):
                            for layer in (pcbnew.B_Cu, pcbnew.In2_Cu):
                                if any(all(cu.segment_ok(p, q, 0.1, net, layer) for p, q in zip(pp, pp[1:])
                                           if math.dist(p, q) > 1e-6) for pp in path_options(o, t)):
                                    add_via(b, cu, net, o, 0.5, 0.25)
                                    add_via(b, cu, net, t, 0.5, 0.25)
                                    connect_path(b, cu, net, o, t, layer, 0.2)
                                    closed = True
                                    break
                        if closed:
                            break
                    if closed:
                        break
                if closed:
                    break
        if not closed and net not in POUR_NETS:
            MAZE_TODO.add(net)
        if not closed and net in POUR_NETS:
            # the net has an inner-layer pour: drop each piece onto it
            closed = all(via_stub(b, cu, net, item_points(it)[0][0], item_points(it)[1][0])
                         for it in items if pcbnew.In2_Cu not in item_points(it)[1])
        if closed:
            done += 1
        else:
            failed.append(f'{net}: {u["items"][0]["description"]} / {u["items"][1]["description"]}')
    return done, failed


def island_vias(b, cu, zone):
    """Put a GND via inside every filled island of `zone` that can take one."""
    ok = True
    layer = zone.GetLayer()
    polys = zone.GetFilledPolysList(layer)
    have = [s_[2][:2] for s_ in cu.segs if s_[0] == 'GND' and s_[4] is not None and s_[4].Type() == pcbnew.PCB_VIA_T]
    for i in range(polys.OutlineCount()):
        # only islands with no ground via yet
        if any(polys.Contains(pcbnew.VECTOR2I(mm(v[0]), mm(v[1])), i) for v in have):
            continue
        ol = polys.Outline(i)
        pts = [xy(ol.CPoint(k)) for k in range(ol.PointCount())]
        if not pts:
            continue
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        placed = False
        for r in [x * 0.25 for x in range(0, 40)]:
            for k in range(16 if r else 1):
                a = 2 * math.pi * k / 16
                p = (cx + r * math.cos(a), cy + r * math.sin(a))
                if polys.Contains(pcbnew.VECTOR2I(mm(p[0]), mm(p[1])), i) and cu.point_ok(*p, 0.3, 'GND', CU, 0.2):
                    add_via(b, cu, 'GND', p)
                    placed = True
                    break
            if placed:
                break
        if not placed:
            print(f'  island on {zone.GetZoneName()} at ({cx - 30:.1f}, {cy - 30:.1f}) board mm, '
                  f'{max(xs) - min(xs):.1f} x {max(ys) - min(ys):.1f} mm: no room for a via')
        ok = ok and placed
    return ok


def fab_cleanup():
    """Warnings a fab would query: vias too close to (or on top of) another hole, and
    pour islands connected to nothing. Drop the offending stitching via; drop dead islands."""
    import tempfile as _t
    with _t.TemporaryDirectory() as td:
        out = os.path.join(td, 'd.json')
        subprocess.run(['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all', '-o', out, PCB],
                       capture_output=True)
        viol = json.load(open(out))['violations']
    drop = set()
    for v in viol:
        if v['type'] in ('hole_to_hole', 'holes_co_located', 'drill_out_of_range'):
            gnd = [i for i in v['items'] if i['description'].startswith('Via [GND]')]
            on_pth = v['type'] == 'holes_co_located' and any(i['description'].startswith('PTH pad')
                                                             for i in v['items'])
            if on_pth:   # a via on a through-hole pad is redundant: the pad reaches every layer
                drop |= {i['uuid'] for i in v['items'] if i['description'].startswith('Via [')}
            elif gnd:
                drop.add(gnd[-1]['uuid'])
    n = strip_uuids(PCB, drop) if drop else 0
    b = pcbnew.LoadBoard(PCB)
    for z in b.Zones():
        z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    pcbnew.SaveBoard(PCB, b)
    print(f'fab cleanup: removed {n} stitching via(s) too close to other holes; dead pour islands dropped')


def main():
    drop_violating_gnd_vias()
    b = pcbnew.LoadBoard(PCB)
    for it in range(4):
        done, failed = fix_pass(b)
        pcbnew.ZONE_FILLER(b).Fill(b.Zones())
        pcbnew.SaveBoard(PCB, b)
        b = pcbnew.LoadBoard(PCB)
        left, _ = drc_unconnected()
        print(f'pass {it + 1}: fixed {done}, still open {len(left)}')
        if not left or not done:
            break
    # anything still open after the geometric passes goes to the maze router
    left, _ = drc_unconnected()
    for u in left:
        d = u['items'][0]['description']
        if '[' in d:
            n_ = d[d.index('[') + 1:d.index(']')]
            if n_ not in POUR_NETS:
                MAZE_TODO.add(n_)
    if MAZE_TODO:
        maze_phase(MAZE_TODO)
        left, _ = drc_unconnected()
        print(f'after maze routing: still open {len(left)}')
        failed = [u['items'][0]['description'] for u in left]
    for f in failed:
        print('  could not close:', f)
    fab_cleanup()


if __name__ == '__main__':
    if len(sys.argv) > 2 and sys.argv[1] == '--maze':
        args = sys.argv[2:]
        group = args[:args.index('--rip')] if '--rip' in args else args
        walls = args[args.index('--rip') + 1:] if '--rip' in args else []
        b0 = pcbnew.LoadBoard(PCB)
        widths = [pcbnew.ToMM(t.GetWidth()) for t in b0.GetTracks()
                  if t.GetNetname() in group and t.Type() == pcbnew.PCB_TRACE_T]
        w = max(0.127, min(widths) if widths else 0.2)
        del b0
        print('MAZE_OK' if maze_attempt(group, w, walls) else 'MAZE_FAIL')
    elif len(sys.argv) > 2 and sys.argv[1] == '--walls-of':
        print('|'.join(find_walls(sys.argv[2:])))
    else:
        main()
