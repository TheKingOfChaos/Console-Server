"""Schematic model: place library symbols, attach every pin to a named net
through a short wire stub and a label / power symbol, and write KiCad
hierarchical schematic files."""
import copy
import math
import re
import uuid as _uuid

from sexp import Sym, dump, find, first, fmt
from kicadlib import unit_pins

GRID = 1.27
POWER_STOCK = {'GND': 'power:GND', '+3.3V': 'power:+3.3V', '+5V': 'power:+5V', '+24V': 'power:+24V'}
POWER_CUSTOM = {'+24V_RAW', '+3V3_MCU', 'PI3.3', 'PI_5V'}
POWER_NETS = set(POWER_STOCK) | POWER_CUSTOM


def uid(seed):
    return str(_uuid.uuid5(_uuid.NAMESPACE_URL, 'usb-console-server/' + seed))


def snap(v):
    return round(round(v / GRID) * GRID, 4)


def rot_vec(x, y, deg):
    """Rotate a y-up vector CCW by deg."""
    r = math.radians(deg)
    c, s = round(math.cos(r)), round(math.sin(r))
    return x * c - y * s, x * s + y * c


def power_lib_id(net):
    return POWER_STOCK.get(net) or f'console_server:{net}'


class Placed:
    def __init__(self, sheet, lib_id, at, ref, value, nets, rot=0, unit=1, fp=None, fields=None,
                 dnp=False, in_bom=True, on_board=True, label_len=2.54, field_side='right'):
        self.sheet, self.lib_id, self.at, self.ref, self.value = sheet, lib_id, at, ref, value
        self.nets, self.rot, self.unit, self.fp = nets, rot, unit, fp
        self.fields = fields or {}
        self.dnp, self.in_bom, self.on_board = dnp, in_bom, on_board
        self.label_len = label_len
        self.field_side = field_side
        self.uuid = uid(f'{sheet.file}/sym/{len(sheet.symbols)}/{lib_id}/{ref}/{unit}')
        self.refs = {}   # instance path -> reference

    def pin_points(self, pins):
        """{num: (abs_x, abs_y, out_dx, out_dy)} in schematic coords (y down)."""
        out = {}
        X, Y = self.at
        for num, p in pins.items():
            px, py = rot_vec(p['x'], p['y'], self.rot)
            ax, ay = snap(X + px), snap(Y - py)
            dx, dy = math.cos(math.radians(p['angle'])), math.sin(math.radians(p['angle']))
            dx, dy = rot_vec(-dx, -dy, self.rot)
            out[num] = (ax, ay, round(dx), -round(dy))
        return out


class Sheet:
    def __init__(self, project, file, title, paper='A3'):
        self.project, self.file, self.title, self.paper = project, file, title, paper
        self.symbols = []
        self.extra = []          # raw sexp items (wires, labels, texts...)
        self.hier = {}           # net -> shape for hierarchical labels
        self.texts = []
        self.sheets = []         # child sheet symbols (root only)
        self.uuid = uid(f'{file}/sheet')

    # -- placement -------------------------------------------------------
    def add(self, lib_id, at, ref, value=None, nets=None, **kw):
        lib_sym, table = self.project.lib.get(lib_id)
        if value is None:
            value = next(p[2] for p in find(lib_sym, 'property') if p[1] == 'Value')
        p = Placed(self, lib_id, (snap(at[0]), snap(at[1])), ref, value, nets or {}, **kw)
        self.symbols.append(p)
        self.project.use(lib_id)
        return p

    def text(self, s, at, size=1.5):
        self.texts.append((s, at, size))

    # -- two-pin helpers (vertical by default) -----------------------------
    def two(self, lib_id, prefix, value, a, b, at, fp, horizontal=False, **kw):
        return self.add(lib_id, at, prefix, value, {'1': a, '2': b}, rot=90 if horizontal else 0, fp=fp, **kw)


class Project:
    def __init__(self, name, lib):
        self.name, self.lib = name, lib
        self.sheets = {}
        self.used = {}
        self.root = None
        self.reserved = set()

    def sheet(self, file, title, paper='A3'):
        s = Sheet(self, file, title, paper)
        self.sheets[file] = s
        return s

    def use(self, lib_id):
        if lib_id not in self.used:
            self.used[lib_id] = self.lib.get(lib_id)[0]


# ------------------------------------------------------------------ writer

def _font(size=1.27, hide=False, justify=None):
    e = [Sym('effects'), [Sym('font'), [Sym('size'), size, size]]]
    if justify:
        e.append([Sym('justify')] + [Sym(j) for j in justify.split()])
    if hide:
        e.append([Sym('hide'), Sym('yes')])
    return e


def _wire(x1, y1, x2, y2, seed):
    return [Sym('wire'), [Sym('pts'), [Sym('xy'), x1, y1], [Sym('xy'), x2, y2]],
            [Sym('stroke'), [Sym('width'), 0], [Sym('type'), Sym('default')]], [Sym('uuid'), uid(seed)]]


def _angle_of(dx, dy):
    return {(1, 0): 0, (0, -1): 90, (-1, 0): 180, (0, 1): 270}[(dx, dy)]


def _label(kind, net, x, y, dx, dy, seed, shape='bidirectional'):
    ang = _angle_of(dx, dy)
    just = 'left' if ang in (0, 90) else 'right'
    if kind == 'label':
        return [Sym('label'), net, [Sym('at'), x, y, ang], [Sym('fields_autoplaced'), Sym('yes')],
                _font(justify=just + ' bottom'), [Sym('uuid'), uid(seed)]]
    if kind == 'global':
        return [Sym('global_label'), net, [Sym('shape'), Sym(shape)], [Sym('at'), x, y, ang],
                [Sym('fields_autoplaced'), Sym('yes')], _font(justify=just), [Sym('uuid'), uid(seed)],
                [Sym('property'), 'Intersheetrefs', '${INTERSHEET_REFS}', [Sym('at'), x, y, 0], _font(hide=True)]]
    return [Sym('hierarchical_label'), net, [Sym('shape'), Sym(shape)], [Sym('at'), x, y, ang],
            [Sym('fields_autoplaced'), Sym('yes')], _font(justify=just), [Sym('uuid'), uid(seed)]]


def _power_rot(lib_pins, dx, dy):
    """Rotation so the power symbol body points along (dx, dy) (schematic coords)."""
    p = next(iter(lib_pins.values()))
    bx, by = math.cos(math.radians(p['angle'])), math.sin(math.radians(p['angle']))
    for r in (0, 90, 180, 270):
        rx, ry = rot_vec(bx, by, r)
        if (round(rx), -round(ry)) == (dx, dy):
            return r
    raise ValueError


class Writer:
    def __init__(self, project, net_kind, hier_shapes):
        self.p = project
        self.net_kind = net_kind          # (sheet_file, net) -> 'label'|'global'|'hier'|'power'
        self.hier_shapes = hier_shapes
        self.pwr_count = 0

    def instances_for(self, sheet):
        return self.p.instance_paths[sheet.file]

    def sym_instance(self, sheet, ps, lib_id, at, rot, unit, refs, value, fp, fields, dnp, in_bom, on_board, seed,
                     field_pos=None):
        lib_sym, table = self.p.lib.get(lib_id)
        pins = unit_pins(table, unit)
        e = [Sym('symbol'), [Sym('lib_id'), lib_id], [Sym('at'), at[0], at[1], rot], [Sym('unit'), unit],
             [Sym('exclude_from_sim'), Sym('no')], [Sym('in_bom'), Sym('yes' if in_bom else 'no')],
             [Sym('on_board'), Sym('yes' if on_board else 'no')], [Sym('dnp'), Sym('yes' if dnp else 'no')],
             [Sym('uuid'), seed]]
        any_ref = next(iter(refs.values()))
        lib_props = {pp[1]: pp for pp in find(lib_sym, 'property')}
        props = [('Reference', any_ref), ('Value', value), ('Footprint', fp if fp is not None else lib_props['Footprint'][2]),
                 ('Datasheet', lib_props.get('Datasheet', [0, 0, ''])[2]),
                 ('Description', lib_props.get('Description', [0, 0, ''])[2])]
        props += list(fields.items())
        for name, val in props:
            visible = name in ('Reference', 'Value')
            if name in ('Reference', 'Value') and any_ref.startswith('#'):
                visible = name == 'Value'
            if field_pos and name in field_pos:
                fx, fy, just = field_pos[name]
                eff = _font(justify=just, hide=not visible)
            elif name in lib_props:
                lat = first(lib_props[name], 'at')
                px, py = rot_vec(float(lat[1]), float(lat[2]), rot)
                fx, fy = at[0] + px, at[1] - py
                eff = _font(hide=not visible)
                le = first(lib_props[name], 'effects')
                lj = first(le, 'justify') if le else None
                if lj and not (rot % 180):
                    eff.insert(2, copy.deepcopy(lj))
            else:
                fx, fy = at
                eff = _font(hide=True)
            e.append([Sym('property'), name, val, [Sym('at'), round(fx, 4), round(fy, 4), rot % 180], eff])
        for num in pins:
            e.append([Sym('pin'), num, [Sym('uuid'), uid(f'{seed}/pin/{num}')]])
        inst = [Sym('project'), self.p.name]
        for path, ref in refs.items():
            inst.append([Sym('path'), path, [Sym('reference'), ref], [Sym('unit'), unit]])
        e.append([Sym('instances'), inst])
        return e

    def connect(self, sheet, out, ps, pin_points, pins):
        done = {}
        # runs of adjacent pins on the same edge and the same power net share one symbol
        runs = {}
        for num, (x, y, dx, dy) in pin_points.items():
            p = pins[num]
            net = ps.nets.get(num)
            if p['hidden'] or p['type'] == 'no_connect' or net not in POWER_NETS:
                continue
            runs.setdefault((dx, dy, net, x if dx else y), []).append((y if dx else x, num))
        skip_symbol = set()
        for (dx, dy, net, fixed), members in runs.items():
            members.sort()
            groups, cur = [], [members[0]]
            for m in members[1:]:
                if m[0] - cur[-1][0] <= 2.55:
                    cur.append(m)
                elif m[0] != cur[-1][0]:
                    groups.append(cur); cur = [m]
            groups.append(cur)
            for g in groups:
                coords = sorted({c for c, _ in g})
                if len(coords) < 2:
                    continue
                L = ps.label_len
                end = snap(fixed + (dx if dx else dy) * L)
                for c1, c2 in zip(coords, coords[1:]):
                    p1, p2 = ((end, c1), (end, c2)) if dx else ((c1, end), (c2, end))
                    out.append(_wire(p1[0], p1[1], p2[0], p2[1], f'{ps.uuid}/tie/{net}/{c1}'))
                for c in coords[1:-1]:
                    jx, jy = (end, c) if dx else (c, end)
                    out.append([Sym('junction'), [Sym('at'), jx, jy], [Sym('diameter'), 0],
                                [Sym('color'), 0, 0, 0, 0], [Sym('uuid'), uid(f'{ps.uuid}/j/{net}/{c}')]])
                keep = g[len(g) // 2][1]
                for _, num in g:
                    if num != keep:
                        skip_symbol.add(num)
        for num, (x, y, dx, dy) in pin_points.items():
            p = pins[num]
            if p['hidden'] or p['type'] == 'no_connect':
                continue
            if num not in ps.nets:
                raise KeyError(f'{sheet.file}: {ps.ref} ({ps.lib_id}) pin {num} {p["name"]} has no net')
            net = ps.nets[num]
            if (x, y) in done:   # stacked pins share one stub
                if done[(x, y)] != net:
                    raise ValueError(f'{sheet.file}: {ps.ref} stacked pin {num} on {net} vs {done[(x, y)]}')
                continue
            done[(x, y)] = net
            seed = f'{ps.uuid}/{num}'
            if net is None:
                out.append([Sym('no_connect'), [Sym('at'), x, y], [Sym('uuid'), uid(seed + '/nc')]])
                continue
            L = ps.label_len
            ex, ey = snap(x + dx * L), snap(y + dy * L)
            out.append(_wire(x, y, ex, ey, seed + '/w'))
            kind = self.net_kind(sheet.file, net)
            if kind == 'power' and num in skip_symbol:
                continue
            if kind == 'power':
                self.power(sheet, out, net, ex, ey, dx, dy, seed)
            elif kind == 'hier':
                out.append(_label('hier', net, ex, ey, dx, dy, seed + '/l', self.hier_shapes.get(net, 'bidirectional')))
            else:
                out.append(_label(kind, net, ex, ey, dx, dy, seed + '/l'))

    def power(self, sheet, out, net, x, y, dx, dy, seed):
        lib_id = power_lib_id(net)
        self.p.use(lib_id)
        _, table = self.p.lib.get(lib_id)
        r = _power_rot(unit_pins(table, 1), dx, dy)
        refs = {}
        for path in self.instances_for(sheet):   # reused sheets: one ref per instance
            self.pwr_count += 1
            refs[path] = f'#PWR{self.pwr_count:04d}'
        sid = uid(seed + '/pwr')
        field_pos = None
        if dy == 0:
            reach = (3.6 if net == 'GND' else 4.0) + 0.55 * len(net)
            field_pos = {'Value': (round(x + dx * reach, 4), y, None)}
        out.append(self.sym_instance(sheet, None, lib_id, (x, y), r, 1, refs, net, '', {}, False, True, True, sid,
                                     field_pos))

    def sheet_file(self, sheet, root_extra=()):
        out = [Sym('kicad_sch'), [Sym('version'), 20250114], [Sym('generator'), 'eeschema'],
               [Sym('generator_version'), '10.0'], [Sym('uuid'), sheet.uuid], [Sym('paper'), sheet.paper],
               [Sym('title_block'), [Sym('title'), sheet.title], [Sym('date'), '2026-10-10'], [Sym('rev'), 'A'],
                [Sym('company'), 'USB Console Server'],
                [Sym('comment'), 1, '48-port RS-232 console server, 16 ports per board'],
                [Sym('comment'), 2, 'Generated from the design record by tools/design.py']]]
        body = []
        used_here = set()
        for ps in sheet.symbols:
            _, table = self.p.lib.get(ps.lib_id)
            pins = unit_pins(table, ps.unit)
            pts = ps.pin_points(pins)
            field_pos = None
            if ps.lib_id in ('Device:R', 'Device:C', 'Device:C_Polarized', 'Device:L', 'Device:FerriteBead_Small',
                             'Device:LED', 'Device:D_Zener', 'Device:D_Schottky', 'Device:Fuse') or ps.lib_id.startswith('Diode:SMAJ'):
                xs = [v[0] for v in pts.values()]
                ys = [v[1] for v in pts.values()]
                cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
                if max(xs) - min(xs) < max(ys) - min(ys):   # vertical part: text to the right
                    half = lambda t: 0.55 * len(t)
                    gap = 2.794 if ps.lib_id in ('Device:R', 'Device:C', 'Device:C_Polarized', 'Device:L',
                                                 'Device:Fuse', 'Device:FerriteBead_Small') else 3.556
                    rtxt = next(iter(ps.refs.values()))
                    field_pos = {'Reference': (cx + gap + half(rtxt), cy - 1.27, None),
                                 'Value': (cx + gap + half(ps.value), cy + 1.27, None)}
                else:
                    field_pos = {'Reference': (cx, cy - 3.302, None), 'Value': (cx, cy + 3.302, None)}
            elif ps.rot % 360 and not ps.lib_id.startswith('power:'):
                xs = [v[0] for v in pts.values()]
                ys = [v[1] for v in pts.values()]
                rtxt = next(iter(ps.refs.values()))
                cx = (min(xs + [ps.at[0]]) + max(xs + [ps.at[0]])) / 2
                field_pos = {'Reference': (cx, min(ys + [ps.at[1]]) - 5.08, None),
                             'Value': (cx, max(ys + [ps.at[1]]) + 5.08, None)}
            fp = ps.fp
            e = self.sym_instance(sheet, ps, ps.lib_id, ps.at, ps.rot, ps.unit, ps.refs, ps.value, fp, ps.fields,
                                  ps.dnp, ps.in_bom, ps.on_board, ps.uuid, field_pos)
            body.append(e)
            self.connect(sheet, body, ps, pts, pins)
        for s, at, size in sheet.texts:
            body.append([Sym('text'), s, [Sym('exclude_from_sim'), Sym('no')], [Sym('at'), at[0], at[1], 0],
                         _font(size, justify='left top'), [Sym('uuid'), uid(f'{sheet.file}/text/{s[:40]}/{at}')]])
        body += list(root_extra)
        lib_symbols = [Sym('lib_symbols')]
        # collect lib ids actually referenced in this file
        ids = []
        def walk(n):
            if isinstance(n, list):
                if n and n[0] == 'lib_id':
                    ids.append(n[1])
                for c in n:
                    walk(c)
        walk(body)
        for lid in sorted(set(ids)):
            lib_symbols.append(copy.deepcopy(self.p.used[lid] if lid in self.p.used else self.p.lib.get(lid)[0]))
        out.append(lib_symbols)
        out += body
        if sheet is self.p.root:
            out.append([Sym('sheet_instances'), [Sym('path'), '/', [Sym('page'), '1']]])
        out.append([Sym('embedded_fonts'), Sym('no')])
        return dump(out) + '\n'


# ------------------------------------------------------------ hierarchy

class SheetRef:
    """A sheet symbol on the root sheet."""
    def __init__(self, name, sheet, at, size, pins, page):
        self.name, self.sheet, self.at, self.size, self.pins, self.page = name, sheet, at, size, pins, page
        self.uuid = uid(f'sheetref/{name}')


def sheet_symbol(p, sr):
    """pins: list of (name, shape, side, net) side in {'left','right'}."""
    x, y = snap(sr.at[0]), snap(sr.at[1])
    w, h = snap(sr.size[0]), snap(sr.size[1])
    e = [Sym('sheet'), [Sym('at'), x, y], [Sym('size'), w, h], [Sym('exclude_from_sim'), Sym('no')],
         [Sym('in_bom'), Sym('yes')], [Sym('on_board'), Sym('yes')], [Sym('dnp'), Sym('no')],
         [Sym('fields_autoplaced'), Sym('yes')],
         [Sym('stroke'), [Sym('width'), 0.1524], [Sym('type'), Sym('solid')]],
         [Sym('fill'), [Sym('color'), 0, 0, 0, 0.0]], [Sym('uuid'), sr.uuid],
         [Sym('property'), 'Sheetname', sr.name, [Sym('at'), x, round(y - 0.7116, 4), 0], _font(justify='left bottom')],
         [Sym('property'), 'Sheetfile', sr.sheet.file, [Sym('at'), x, round(y + h + 0.5846, 4), 0],
          _font(justify='left top', hide=False)]]
    items = []
    counts = {'left': 0, 'right': 0}
    for name, shape, side, net in sr.pins:
        counts[side] += 1
        py = snap(y + 2.54 * counts[side])
        px = x if side == 'left' else x + w
        e.append([Sym('pin'), name, Sym(shape), [Sym('at'), px, py, 180 if side == 'left' else 0],
                  [Sym('uuid'), uid(f'{sr.uuid}/pin/{name}')], _font(justify='left' if side == 'left' else 'right')])
        dx = -1 if side == 'left' else 1
        ex = snap(px + dx * 5.08)
        items.append(_wire(px, py, ex, py, f'{sr.uuid}/w/{name}'))
        if net in POWER_NETS:
            items.append(('power', net, ex, py, dx, 0, f'{sr.uuid}/p/{name}'))
        else:
            items.append(_label('global', net, ex, py, dx, 0, f'{sr.uuid}/l/{name}', shape))
    e.append([Sym('instances'), [Sym('project'), p.name,
                                 [Sym('path'), '/' + p.root.uuid, [Sym('page'), str(sr.page)]]]])
    return e, items


def allocate_refs(p, order):
    """Assign references per instance path. Explicit refs are kept; prefix-only refs
    are numbered in sheet order, skipping explicitly used numbers."""
    used = set()
    for file in order:
        sh = p.sheets[file]
        for i, path in enumerate(p.instance_paths[file]):
            for ps in sh.symbols:
                r = ps.ref(i) if callable(ps.ref) else ps.ref
                if re.search(r'\d$', r):
                    used.add(r)
    counters = {}
    multi = {}   # (sheet, symbol-group key, path) -> ref for multi-unit symbols sharing a ref
    for file in order:
        sh = p.sheets[file]
        for i, path in enumerate(p.instance_paths[file]):
            for ps in sh.symbols:
                r = ps.ref(i) if callable(ps.ref) else ps.ref
                if not re.search(r'\d$', r):
                    n = counters.get(r, 0) + 1
                    while f'{r}{n}' in used:
                        n += 1
                    counters[r] = n
                    r = f'{r}{n}'
                    used.add(r)
                ps.refs[path] = r
