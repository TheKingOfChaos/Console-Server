"""Small grid maze router for the last few connections the autorouter could not finish.

Copper of every other net is rasterised per layer onto a 0.1 mm grid, grown by
clearance + half the track width (and, for via sites, by clearance + via radius on
every layer). A* then searches F.Cu / In2.Cu / B.Cu with 8-way moves and via moves,
and the path is simplified into straight tracks and vias.
"""
import heapq
import math

import numpy as np
import pcbnew

RES = 0.05
LAYERS = [pcbnew.F_Cu, pcbnew.In2_Cu, pcbnew.B_Cu]
ALL_CU = [pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.B_Cu]


def _mm(v):
    return pcbnew.ToMM(v)


class Grid:
    def __init__(self, b, net, box, width, clearance, via_dia):
        own = {net} if isinstance(net, str) else set(net)
        self.x0, self.y0, x1, y1 = box
        self.nx = int((x1 - self.x0) / RES) + 1
        self.ny = int((y1 - self.y0) / RES) + 1
        self.block = {l: np.zeros((self.ny, self.nx), bool) for l in LAYERS}
        self.vblock = np.zeros((self.ny, self.nx), bool)
        tr = clearance + width / 2
        vr = clearance + via_dia / 2
        for f in b.GetFootprints():
            for p in f.Pads():
                if p.GetNetname() in own:
                    continue
                bb = p.GetBoundingBox()
                box_ = (_mm(bb.GetLeft()), _mm(bb.GetTop()), _mm(bb.GetRight()), _mm(bb.GetBottom()))
                for l in LAYERS:
                    if p.IsOnLayer(l):
                        self._rect(self.block[l], box_, tr)
                if any(p.IsOnLayer(l) for l in ALL_CU) or p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
                    self._rect(self.vblock, box_, vr)
        for t in b.GetTracks():
            if t.GetNetname() in own:
                continue
            if t.Type() == pcbnew.PCB_VIA_T:
                x, y = _mm(t.GetPosition().x), _mm(t.GetPosition().y)
                r = _mm(t.GetWidth(pcbnew.F_Cu)) / 2
                for l in LAYERS:
                    self._seg(self.block[l], (x, y, x, y), r + tr)
                self._seg(self.vblock, (x, y, x, y), r + vr)
            else:
                s = (_mm(t.GetStart().x), _mm(t.GetStart().y), _mm(t.GetEnd().x), _mm(t.GetEnd().y))
                hw = _mm(t.GetWidth()) / 2
                if t.GetLayer() in LAYERS:
                    self._seg(self.block[t.GetLayer()], s, hw + tr)
                self._seg(self.vblock, s, hw + vr)
        # rule areas that forbid tracks (e.g. the In2 keepouts over the USB ground strips)
        for z in b.Zones():
            try:
                if not (z.GetIsRuleArea() and z.GetDoNotAllowTracks()):
                    continue
                pts = [(_mm(z.GetCornerPosition(i).x), _mm(z.GetCornerPosition(i).y)) for i in range(z.GetNumCorners())]
            except Exception:
                continue
            for l in LAYERS:
                if z.IsOnLayer(l) and pts:
                    self._poly(self.block[l], pts, width / 2 + clearance)
        # board edge keep-in
        for l in LAYERS:
            self._edge(self.block[l], 0.3 + width / 2)
        self._edge(self.vblock, 0.3 + via_dia / 2)

    def cell(self, x, y):
        return int(round((y - self.y0) / RES)), int(round((x - self.x0) / RES))

    def pos(self, i, j):
        return self.x0 + j * RES, self.y0 + i * RES

    def _rect(self, g, box, grow):
        x1, y1, x2, y2 = box
        i1, j1 = self.cell(x1 - grow, y1 - grow)
        i2, j2 = self.cell(x2 + grow, y2 + grow)
        g[max(i1, 0):max(i2 + 1, 0), max(j1, 0):max(j2 + 1, 0)] = True

    def _seg(self, g, s, r):
        x1, y1, x2, y2 = s
        i1, j1 = self.cell(min(x1, x2) - r, min(y1, y2) - r)
        i2, j2 = self.cell(max(x1, x2) + r, max(y1, y2) + r)
        i1, j1, i2, j2 = max(i1, 0), max(j1, 0), min(i2, self.ny - 1), min(j2, self.nx - 1)
        if i1 > i2 or j1 > j2:
            return
        ys = self.y0 + np.arange(i1, i2 + 1) * RES
        xs = self.x0 + np.arange(j1, j2 + 1) * RES
        X, Y = np.meshgrid(xs, ys)
        dx, dy = x2 - x1, y2 - y1
        L = dx * dx + dy * dy
        t = np.zeros_like(X) if L == 0 else np.clip(((X - x1) * dx + (Y - y1) * dy) / L, 0, 1)
        d = np.hypot(X - x1 - t * dx, Y - y1 - t * dy)
        g[i1:i2 + 1, j1:j2 + 1] |= d < r

    def _poly(self, g, pts, grow):
        """Fill a polygon (even-odd), grown by `grow` via its edges."""
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        i1, j1 = self.cell(min(xs) - grow, min(ys) - grow)
        i2, j2 = self.cell(max(xs) + grow, max(ys) + grow)
        i1, j1, i2, j2 = max(i1, 0), max(j1, 0), min(i2, self.ny - 1), min(j2, self.nx - 1)
        if i1 > i2 or j1 > j2:
            return
        Y, X = np.mgrid[i1:i2 + 1, j1:j2 + 1]
        X = self.x0 + X * RES
        Y = self.y0 + Y * RES
        inside = np.zeros(X.shape, bool)
        n = len(pts)
        for k in range(n):
            (xa, ya), (xb, yb) = pts[k], pts[(k + 1) % n]
            cond = (ya > Y) != (yb > Y)
            xint = (xb - xa) * (Y - ya) / ((yb - ya) if yb != ya else 1e-12) + xa
            inside ^= cond & (X < xint)
        g[i1:i2 + 1, j1:j2 + 1] |= inside
        for k in range(n):
            (xa, ya), (xb, yb) = pts[k], pts[(k + 1) % n]
            self._seg(g, (xa, ya, xb, yb), grow)

    def _edge(self, g, r):
        k = int(math.ceil(r / RES))
        g[:k, :] = g[-k:, :] = True
        g[:, :k] = g[:, -k:] = True


def route(b, net, start, goal, start_layers, goal_layers, width=0.16, clearance=0.15, via=(0.5, 0.25),
          margin=8.0, via_cost=25, board_box=None, extra_goals=()):
    """A* from start to goal (plus any extra goal points). Returns (runs, vias) or None."""
    pts_all = [start, goal] + [p for p, _ in extra_goals]
    x1, y1 = min(p[0] for p in pts_all) - margin, min(p[1] for p in pts_all) - margin
    x2, y2 = max(p[0] for p in pts_all) + margin, max(p[1] for p in pts_all) + margin
    if board_box:
        bx1, by1, bx2, by2 = board_box
        x1, y1, x2, y2 = max(x1, bx1), max(y1, by1), min(x2, bx2), min(y2, by2)
    g = Grid(b, net, (x1, y1, x2, y2), width, clearance, via[0])
    s_ij, g_ij = g.cell(*start), g.cell(*goal)
    # own pads are free at the ends
    for l in LAYERS:
        for (i, j) in (s_ij, g_ij):
            g.block[l][max(i - 3, 0):i + 4, max(j - 3, 0):j + 4] = False
    starts = [(LAYERS.index(l), *s_ij) for l in start_layers if l in LAYERS]
    goals = {(LAYERS.index(l), *g_ij) for l in goal_layers if l in LAYERS}
    for p, ls in extra_goals:
        ij = g.cell(*p)
        if 0 <= ij[0] < g.ny and 0 <= ij[1] < g.nx:
            goals |= {(LAYERS.index(l), *ij) for l in ls if l in LAYERS}
    if not starts or not goals:
        return None
    gi, gj = g_ij

    def h(i, j):
        return 0.0 if extra_goals else math.hypot(i - gi, j - gj)
    openq = [(h(i, j), 0.0, (k, i, j)) for k, i, j in starts]
    came, cost = {}, {n: 0.0 for n in [(k, i, j) for k, i, j in starts]}
    moves = [(-1, 0, 1), (1, 0, 1), (0, -1, 1), (0, 1, 1), (-1, -1, 1.414), (-1, 1, 1.414), (1, -1, 1.414), (1, 1, 1.414)]
    found = None
    while openq:
        _, c, node = heapq.heappop(openq)
        if node in goals:
            found = node
            break
        if c > cost.get(node, 1e18):
            continue
        k, i, j = node
        nbrs = []
        for di, dj, w in moves:
            ni, nj = i + di, j + dj
            if 0 <= ni < g.ny and 0 <= nj < g.nx and not g.block[LAYERS[k]][ni, nj]:
                nbrs.append(((k, ni, nj), w))
        if not g.vblock[i, j]:
            for k2 in range(len(LAYERS)):
                if k2 != k and not g.block[LAYERS[k2]][i, j]:
                    nbrs.append(((k2, i, j), via_cost))
        for n2, w in nbrs:
            c2 = c + w
            if c2 < cost.get(n2, 1e18):
                cost[n2] = c2
                came[n2] = node
                heapq.heappush(openq, (c2 + h(n2[1], n2[2]), c2, n2))
    if not found:
        return None
    path = [found]
    while path[-1] in came:
        path.append(came[path[-1]])
    path.reverse()
    runs, vias, cur = [], [], [path[0]]
    for a, c in zip(path, path[1:]):
        if a[0] != c[0]:
            runs.append((LAYERS[cur[0][0]], cur))
            vias.append(g.pos(a[1], a[2]))
            cur = [c]
        else:
            cur.append(c)
    runs.append((LAYERS[cur[0][0]], cur))
    out = []
    for layer, nodes in runs:
        pts = [g.pos(n[1], n[2]) for n in nodes]
        simp = [pts[0]]
        for p in pts[1:-1]:
            q = simp[-1]
            dir1 = (round(p[0] - q[0], 6), round(p[1] - q[1], 6))
            if len(simp) >= 2:
                r_ = simp[-2]
                dir0 = (round(q[0] - r_[0], 6), round(q[1] - r_[1], 6))
                if abs(dir0[0] * dir1[1] - dir0[1] * dir1[0]) < 1e-9:
                    simp[-1] = p
                    continue
            simp.append(p)
        if len(pts) > 1:
            simp.append(pts[-1])
        out.append((layer, simp))
    return out, vias


def commit(b, net, result, width=0.16, via=(0.5, 0.25), start=None, goal=None, start_layers=None,
           goal_layers=None):
    runs, vias = result
    vias = list(vias)
    # every layer change needs a via, also one at the very first/last cell (A* can skip recording it)
    for (l1, p1), (l2, p2) in zip(runs, runs[1:]):
        if l1 != l2 and p1 and p2:
            q = p2[0]
            if not any(math.dist(q, v) < 0.05 for v in vias):
                vias.append(q)
    netinfo = b.FindNet(net)
    for i, (layer, pts) in enumerate(runs):
        # hook onto the pad only on a layer the pad is actually on
        if i == 0 and start and (not start_layers or layer in start_layers):
            pts = [start] + pts
        if i == len(runs) - 1 and goal and (not goal_layers or layer in goal_layers):
            pts = pts + [goal]
        for a, c in zip(pts, pts[1:]):
            if math.dist(a, c) < 1e-6:
                continue
            t = pcbnew.PCB_TRACK(b)
            t.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(a[0]), pcbnew.FromMM(a[1])))
            t.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(c[0]), pcbnew.FromMM(c[1])))
            t.SetLayer(layer)
            t.SetWidth(pcbnew.FromMM(width))
            t.SetNet(netinfo)
            b.Add(t)
    for p in vias:
        v = pcbnew.PCB_VIA(b)
        v.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(p[0]), pcbnew.FromMM(p[1])))
        v.SetWidth(pcbnew.FromMM(via[0]))
        v.SetDrill(pcbnew.FromMM(via[1]))
        v.SetNet(netinfo)
        b.Add(v)


def sealed_walls(b, net, point, layers, width=0.16, clearance=0.15, radius=6.0, limit=4000):
    """If `point` is sealed in (flood fill reaches fewer than `limit` cells), return the
    nets whose tracks border the reachable pocket; otherwise an empty set."""
    import collections
    box = (point[0] - radius, point[1] - radius, point[0] + radius, point[1] + radius)
    g = Grid(b, net, box, width, clearance, 0.5)
    i0, j0 = g.cell(*point)
    for l in LAYERS:
        g.block[l][max(i0 - 3, 0):i0 + 4, max(j0 - 3, 0):j0 + 4] = False
    seen = {(LAYERS.index(l), i0, j0) for l in layers if l in LAYERS}
    q = collections.deque(seen)
    while q and len(seen) < limit:
        k, i, j = q.popleft()
        nb = [(k, i + di, j + dj) for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1))]
        if not g.vblock[i, j]:
            nb += [(k2, i, j) for k2 in range(len(LAYERS)) if k2 != k]
        for n in nb:
            kk, ii, jj = n
            if 0 <= ii < g.ny and 0 <= jj < g.nx and n not in seen and not g.block[LAYERS[kk]][ii, jj]:
                seen.add(n)
                q.append(n)
    if len(seen) >= limit:
        return set()
    cells = [g.pos(i, j) for k, i, j in seen]
    walls = set()
    reach = clearance + width / 2 + 0.25
    for t in b.GetTracks():
        n = t.GetNetname()
        if n == net or t.IsLocked() or t.Type() == pcbnew.PCB_VIA_T:
            continue
        s = (_mm(t.GetStart().x), _mm(t.GetStart().y), _mm(t.GetEnd().x), _mm(t.GetEnd().y))
        hw = _mm(t.GetWidth()) / 2
        if t.GetLayer() not in LAYERS:
            continue
        for (x, y) in cells[::3]:
            dx, dy = s[2] - s[0], s[3] - s[1]
            L = dx * dx + dy * dy
            tt = 0 if L == 0 else max(0, min(1, ((x - s[0]) * dx + (y - s[1]) * dy) / L))
            if math.hypot(x - s[0] - tt * dx, y - s[1] - tt * dy) < hw + reach:
                walls.add(n)
                break
    return walls
