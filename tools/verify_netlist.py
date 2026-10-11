"""Cross-check: export the netlist with kicad-cli and compare every pin's connectivity
against the nets the generator intended. Any merge or split is reported."""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import design
from sexp import parse, find, first
from kicadlib import unit_pins
from schgen import POWER_NETS


def intended():
    P = design.P
    groups = {}
    for f, sh in P.sheets.items():
        for i, path in enumerate(P.instance_paths[f]):
            for ps in sh.symbols:
                ref = ps.refs[path]
                if ref.startswith('#'):
                    continue
                _, table = P.lib.get(ps.lib_id)
                pins = unit_pins(table, ps.unit)
                for num, net in ps.nets.items():
                    if net is None:
                        continue
                    # global scope: power nets, global labels; hier/local: per instance
                    kind = design.classify(P)[0](f, net)
                    if kind == 'hier':
                        sr = next(s for s in P.sheet_refs if path.endswith(s.uuid))
                        net = next(n for (pn, _, _, n) in sr.pins if pn == net)
                        scope = ''
                    elif kind in ('power', 'global'):
                        scope = ''
                    else:
                        scope = path
                    groups.setdefault((scope, net), set()).add((ref, num))
                    # stacked hidden pins share the net
                    vis = pins[num]
                    for n2, p2 in pins.items():
                        if p2['hidden'] and p2['type'] != 'no_connect' and (p2['x'], p2['y']) == (vis['x'], vis['y']):
                            groups[(scope, net)].add((ref, n2))
    return groups


def actual(netfile):
    root = parse(open(netfile).read())
    nets = {}
    for n in find(first(root, 'nets'), 'net'):
        name = first(n, 'name')[1]
        nodes = {(first(x, 'ref')[1], first(x, 'pin')[1]) for x in find(n, 'node')}
        nets[name] = nodes
    return nets


def main():
    design.build()
    proj = design.OUT
    with tempfile.TemporaryDirectory() as td:
        net = os.path.join(td, 'n.net')
        subprocess.run(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadsexpr', '-o', net,
                        os.path.join(proj, f'{design.PROJ}.kicad_sch')], check=True, capture_output=True)
        act = actual(net)
    want = intended()
    pin_to_act = {}
    for name, nodes in act.items():
        for nd in nodes:
            pin_to_act[nd] = name
    problems = 0
    for (scope, net), pins in sorted(want.items(), key=lambda kv: kv[0][1]):
        names = {pin_to_act.get(p, '<unconnected>') for p in pins}
        if len(names) != 1:
            problems += 1
            print(f'SPLIT  {net} ({scope[-8:] or "global"}): {sorted(names)}')
            continue
        a = names.pop()
        extra = act.get(a, set()) - pins
        if extra:
            problems += 1
            print(f'MERGED {net}: actual net {a} also has {sorted(extra)[:6]}')
    print(f'{len(want)} intended nets, {len(act)} netlist nets, {problems} problems')
    return problems


if __name__ == '__main__':
    sys.exit(1 if main() else 0)
