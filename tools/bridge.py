"""Join islands of a (pour) net without ripping anything: for each unconnected pair DRC reports
on NET, maze-route a track between the two items (any layer, keepouts honoured) and commit it.
One pair per process (repeated LoadBoard is unstable).

    python3 tools/bridge.py NET [width]
"""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from close_gaps import BOARD_BOX, PCB, net_clearance  # noqa: E402


def pairs(net):
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, 'd.json')
        subprocess.run(['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-error', '-o', out, PCB],
                       capture_output=True)
        d = json.load(open(out))
    res = []
    for u in d['unconnected_items']:
        its = u['items']
        if len(its) == 2 and all(f'[{net}]' in i['description'] for i in its) and 'Zone' not in its[0]['description']:
            res.append([((i['pos']['x'], i['pos']['y']), i['description']) for i in its])
    return res


def layers_of(desc):
    import maze
    import pcbnew
    m = re.search(r' on ([FB]\.Cu|In\d\.Cu)$', desc)
    if desc.startswith('Via') or desc.startswith('PTH') or not m:
        return list(maze.LAYERS)
    return [{'F.Cu': pcbnew.F_Cu, 'B.Cu': pcbnew.B_Cu, 'In1.Cu': pcbnew.In1_Cu, 'In2.Cu': pcbnew.In2_Cu}[m.group(1)]]


def one(net, width, idx):
    import maze
    import pcbnew
    ps = pairs(net)
    if idx >= len(ps):
        print('NONE')
        return
    (a, da), (c, dc) = ps[idx]
    la, lc = layers_of(da), layers_of(dc)
    b = pcbnew.LoadBoard(PCB)
    res = maze.route(b, net, a, c, la, lc, width=width, clearance=max(0.15, net_clearance(net)),
                     margin=8, board_box=BOARD_BOX)
    if not res:
        print('FAIL', da, '->', dc)
        return
    maze.commit(b, net, res, width=width, start=a, goal=c, start_layers=la, goal_layers=lc)
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    pcbnew.SaveBoard(PCB, b)
    print('OK', da, '->', dc)


def main():
    net = sys.argv[1]
    width = float(sys.argv[2]) if len(sys.argv) > 2 else 0.2
    if len(sys.argv) > 3:
        one(net, width, int(sys.argv[3]))
        return
    skip = 0
    seen = set()
    for _ in range(40):
        r = subprocess.run([sys.executable, __file__, net, str(width), str(skip)], capture_output=True, text=True)
        line = (r.stdout.strip().splitlines() or ['?'])[-1]
        print('  ', line)
        if line.startswith('NONE') or line == '?':
            break
        if line.startswith('FAIL'):
            skip += 1
        elif line in seen:          # "OK" but DRC still reports the same pair: it did not connect
            print('   (no progress on this pair, stopping)')
            break
        seen.add(line)


if __name__ == '__main__':
    main()
