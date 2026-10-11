"""Symbol library access: load stock KiCad symbols, flatten `extends`,
and build the project-specific symbols."""
import copy
import math
import os

from sexp import Sym, parse, find, first

STOCK_SYM = '/usr/share/kicad/symbols'


def _rename_units(sym, old, new):
    for u in find(sym, 'symbol'):
        if u[1].startswith(old + '_'):
            u[1] = new + u[1][len(old):]


class SymbolLib:
    """Caches symbol definitions as flattened S-expressions keyed by lib_id."""

    def __init__(self):
        self._files = {}
        self.custom = {}          # name -> sexp (lib "console_server")

    def _file(self, lib):
        if lib not in self._files:
            root = parse(open(f'{STOCK_SYM}/{lib}.kicad_sym').read())
            self._files[lib] = {s[1]: s for s in find(root, 'symbol')}
        return self._files[lib]

    def _flat(self, lib, name):
        syms = self._file(lib)
        s = copy.deepcopy(syms[name])
        ext = first(s, 'extends')
        if not ext:
            return s
        parent = self._flat(lib, ext[1])
        out = copy.deepcopy(parent)
        out[1] = name
        _rename_units(out, ext[1], name)
        child_props = {p[1]: p for p in find(s, 'property')}
        for i, e in enumerate(out):
            if isinstance(e, list) and e and e[0] == 'property' and e[1] in child_props:
                out[i] = child_props.pop(e[1])
        idx = max(i for i, e in enumerate(out) if isinstance(e, list) and e and e[0] == 'property')
        for p in child_props.values():
            idx += 1
            out.insert(idx, p)
        return out

    def get(self, lib_id):
        """Return (flattened sexp named lib_id, pin table)."""
        lib, name = lib_id.split(':', 1)
        if lib == 'console_server':
            s = copy.deepcopy(self.custom[name])
        else:
            s = self._flat(lib, name)
        s[1] = lib_id
        return s, pin_table(s)


def pin_table(sym):
    """{unit: {number: dict(x, y, angle, length, name, type, hidden)}} with unit 0 = common."""
    name = sym[1].split(':')[-1]
    units = {}
    for u in find(sym, 'symbol'):
        tail = u[1][len(name) + 1:] if u[1].startswith(name + '_') else u[1].rsplit('_', 2)[-2] + '_' + u[1].rsplit('_', 1)[-1]
        unit, style = (int(v) for v in tail.split('_'))
        if style not in (0, 1):
            continue
        for p in find(u, 'pin'):
            at = first(p, 'at')
            hidden = any(e == 'hide' or (isinstance(e, list) and e[:2] == ['hide', 'yes']) for e in p)
            units.setdefault(unit, {})[str(first(p, 'number')[1])] = dict(
                x=float(at[1]), y=float(at[2]), angle=float(at[3]) if len(at) > 3 else 0.0,
                length=float(first(p, 'length')[1]), name=first(p, 'name')[1],
                type=str(p[1]), hidden=hidden)
    return units


def unit_pins(table, unit):
    pins = dict(table.get(0, {}))
    pins.update(table.get(unit, {}))
    return pins


# ---------------------------------------------------------------- builders

def _font(size=1.27):
    return [Sym('effects'), [Sym('font'), [Sym('size'), size, size]]]


def _prop(name, value, x=0, y=0, hide=False, rot=0):
    eff = _font()
    if hide:
        eff.append([Sym('hide'), Sym('yes')])
    return [Sym('property'), name, value, [Sym('at'), x, y, rot], eff]


def _pin(num, name, x, y, angle, typ='passive', length=2.54, hide=False):
    p = [Sym('pin'), Sym(typ), Sym('line'), [Sym('at'), x, y, angle], [Sym('length'), length]]
    if hide:
        p.append([Sym('hide'), Sym('yes')])
    p += [[Sym('name'), name, _font()], [Sym('number'), str(num), _font()]]
    return p


def _rect(x1, y1, x2, y2):
    return [Sym('rectangle'), [Sym('start'), x1, y1], [Sym('end'), x2, y2],
            [Sym('stroke'), [Sym('width'), 0.254], [Sym('type'), Sym('default')]],
            [Sym('fill'), [Sym('type'), Sym('background')]]]


def _text(t, x, y, size=1.27):
    return [Sym('text'), t, [Sym('at'), x, y, 0], _font(size)]


def _set_prop_at(s, name, x, y):
    for e in s:
        if isinstance(e, list) and len(e) > 2 and e[0] == 'property' and e[1] == name:
            e[3] = [Sym('at'), x, y, 0]


def _symbol_shell(name, ref, value, fp='', ds='', desc='', pin_names_offset=1.016, hide_pin_numbers=False):
    s = [Sym('symbol'), name]
    if hide_pin_numbers:
        s.append([Sym('pin_numbers'), [Sym('hide'), Sym('yes')]])
    s += [[Sym('pin_names'), [Sym('offset'), pin_names_offset]],
          [Sym('exclude_from_sim'), Sym('no')], [Sym('in_bom'), Sym('yes')], [Sym('on_board'), Sym('yes')],
          _prop('Reference', ref, 0, 0), _prop('Value', value, 0, -2.54),
          _prop('Footprint', fp, 0, 0, hide=True), _prop('Datasheet', ds, 0, 0, hide=True),
          _prop('Description', desc, 0, 0, hide=True)]
    return s


def box_symbol(name, ref, value, left, right, fp='', ds='', desc='', top=(), bottom=(), width=15.24):
    """Rectangular IC symbol. left/right/top/bottom: lists of (num, name, type) or None for a gap."""
    rows = max(len(left), len(right), 1)
    h = (rows + 1) * 2.54
    half_w = width / 2
    top_y = round(h / 2 / 2.54) * 2.54
    s = _symbol_shell(name, ref, value, fp, ds, desc)
    _set_prop_at(s, 'Reference', 0, top_y + 1.27)
    _set_prop_at(s, 'Value', 0, top_y - (rows + 1) * 2.54 - 1.27)
    body = [Sym('symbol'), f'{name}_0_1', _rect(-half_w, top_y, half_w, top_y - (rows + 1) * 2.54)]
    pins = [Sym('symbol'), f'{name}_1_1']
    for i, p in enumerate(left):
        if p:
            pins.append(_pin(p[0], p[1], -half_w - 2.54, top_y - 2.54 * (i + 1), 0, p[2] if len(p) > 2 else 'passive'))
    for i, p in enumerate(right):
        if p:
            pins.append(_pin(p[0], p[1], half_w + 2.54, top_y - 2.54 * (i + 1), 180, p[2] if len(p) > 2 else 'passive'))
    s += [body, pins]
    return s


def clone_fet(lib, base, name, mapping, value, fp, extra=None):
    """Clone Device:Q_xMOS renumbering D/G/S pins; extra = {letter: [more pin numbers]} stacked + hidden."""
    s, _ = lib.get(f'Device:{base}')
    s = copy.deepcopy(s)
    s[1] = name
    _rename_units(s, base, name)
    for u in find(s, 'symbol'):
        new = []
        for e in u:
            new.append(e)
            if isinstance(e, list) and e and e[0] == 'pin':
                letter = first(e, 'number')[1]
                first(e, 'number')[1] = mapping[letter]
                for n in (extra or {}).get(letter, []):
                    dup = copy.deepcopy(e)
                    first(dup, 'number')[1] = n
                    dup.insert(5, [Sym('hide'), Sym('yes')])
                    new.append(dup)
        u[:] = new
    for p in find(s, 'property'):
        if p[1] == 'Value':
            p[2] = value
        if p[1] == 'Footprint':
            p[2] = fp
    pn = first(s, 'pin_numbers')
    if pn:
        s.remove(pn)
    return s


def power_symbol(lib, base, name):
    """Clone a power symbol (power:+24V style) under a new net name."""
    s, _ = lib.get(f'power:{base}')
    s = copy.deepcopy(s)
    s[1] = name
    _rename_units(s, base, name)
    for p in find(s, 'property'):
        if p[1] == 'Value':
            p[2] = name
        if p[1] == 'Description':
            p[2] = f'Power symbol creates a global label with name "{name}"'
    for u in find(s, 'symbol'):
        for e in u:
            if isinstance(e, list) and e and e[0] == 'pin':
                first(e, 'name')[1] = name
    return s


def rj45_block(name='HC-RJ45-059D-2x8'):
    """16-port ganged RJ45 block: units 1-16 = ports, unit 17 = shell + unused LED pins.

    Port P(2j+1) (lower jack, column j) contact c -> pad 24j+c
    Port P(2j+2) (upper jack, column j) contact c -> pad 24j+8+c
    LED pads 24j+17..24j+24, shell pads 193-196.
    """
    s = _symbol_shell(name, 'J', 'HC-RJ45-059D-2x8-2-04', fp='console_server:RJ45_HC-RJ45-059D-2x8',
                      ds='https://www.lcsc.com/product-detail/C46261623.html',
                      desc='2x8 ganged RJ45 block, shielded, no magnetics', pin_names_offset=0.762)
    _set_prop_at(s, 'Reference', 0, 12.7)
    _set_prop_at(s, 'Value', 0, -12.7)
    labels = {1: 'NC', 2: 'NC', 3: 'RxD', 4: 'GND', 5: 'GND', 6: 'TxD', 7: 'NC', 8: 'NC'}
    for port in range(1, 17):
        j = (port - 1) // 2
        base = 24 * j + (0 if port % 2 else 8)
        u = [Sym('symbol'), f'{name}_{port}_1', _rect(-5.08, 11.43, 5.08, -11.43),
             _text(f'P{port:02d}', 0, 12.7 - 2.54, 1.27)]
        for c in range(1, 9):
            u.append(_pin(base + c, f'{c} {labels[c]}', -7.62, 8.89 - 2.54 * (c - 1), 0, 'passive'))
        s.append(u)
    shell = [Sym('symbol'), f'{name}_17_1', _rect(-5.08, 5.08, 5.08, -5.08), _text('SHELL', 0, 0, 1.27)]
    for i, n in enumerate((193, 194, 195, 196)):
        shell.append(_pin(n, 'SHIELD', 0, -7.62, 90, 'passive', hide=(i > 0)))
    for j in range(8):
        for k in range(17, 25):
            shell.append(_pin(24 * j + k, 'LED', 0, 7.62, 270, 'no_connect', hide=True))
    s.append(shell)
    return s


def build_custom(lib):
    c = {}
    c['PRTR5V0U2F'] = box_symbol(
        'PRTR5V0U2F', 'U', 'PRTR5V0U2F',
        left=[(1, 'IO1'), None, (3, 'IO2')], right=[(6, 'IO1'), None, (4, 'IO2')],
        fp='console_server:SOT886_L1.5-W1.0-P0.50-LS1.00-BL',
        ds='https://assets.nexperia.com/documents/data-sheet/PRTR5V0U2F_PRTR5V0U2K.pdf',
        desc='Dual-line USB ESD protection, rail-to-rail, SOT886', width=10.16)
    # VCC / GND on the bottom edge via left/right extension
    c['PRTR5V0U2F'][-1] += [_pin(5, 'VCC', -2.54, 7.62, 270, 'passive'), _pin(2, 'GND', -2.54, -7.62 - 2.54, 90, 'passive')]
    c['PRTR5V0U2F'][-2][2] = _rect(-5.08, 5.08, 5.08, -7.62)
    c['93LC46B'] = box_symbol(
        '93LC46B', 'U', '93LC46BT-I/SN',
        left=[(1, 'CS', 'input'), (2, 'CLK', 'input'), (3, 'DI', 'input'), (4, 'DO', 'output')],
        right=[(8, 'VCC', 'power_in'), (6, 'NC', 'no_connect'), (7, 'NC', 'no_connect'), (5, 'VSS', 'power_in')],
        fp='Package_SO:SOIC-8_3.9x4.9mm_P1.27mm',
        ds='https://ww1.microchip.com/downloads/en/DeviceDoc/20001749K.pdf',
        desc='1 Kbit Microwire EEPROM, x16 organisation', width=12.7)
    c['IRFR5305'] = clone_fet(lib, 'Q_PMOS', 'IRFR5305', {'G': '1', 'D': '2', 'S': '3'}, 'IRFR5305TRPBF',
                              'console_server:TO-252-3_L6.5-W6.1-P4.58-LS9.9-BR')
    c['HYG032N03LR1C1'] = clone_fet(lib, 'Q_NMOS', 'HYG032N03LR1C1', {'S': '1', 'G': '4', 'D': '5'}, 'HYG032N03LR1C1',
                                    'console_server:DFN-8_L3.0-W3.0-P0.65-BL-2',
                                    extra={'S': ['2', '3'], 'D': ['6', '7', '8', '9']})
    c['HC-RJ45-059D-2x8'] = rj45_block()
    for net, base in (('+24V_RAW', '+24V'), ('+3V3_MCU', '+3.3V'), ('PI3.3', '+3.3V'), ('PI_5V', '+5V')):
        c[net] = power_symbol(lib, base, net)
    lib.custom.update(c)
    return c


def write_library(lib, path):
    from sexp import dump
    out = [Sym('kicad_symbol_lib'), [Sym('version'), 20251024], [Sym('generator'), 'console_server_gen'],
           [Sym('generator_version'), '10.0']]
    for name, s in lib.custom.items():
        out.append(copy.deepcopy(s))
    with open(path, 'w') as f:
        f.write(dump(out) + '\n')
