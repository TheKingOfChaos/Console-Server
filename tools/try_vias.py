"""Try GND via candidates (optionally with a stub from a pad), keep the first that DRC accepts."""
import json, subprocess, sys, tempfile, shutil, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from close_gaps import PCB


def drc():
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, 'd.json')
        subprocess.run(['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-error', '-o', out, PCB], capture_output=True)
        d = json.load(open(out))
    return len(d['violations']), len(d['unconnected_items'])


def add(p, stub, d, dr):
    code = f'''
import pcbnew
b = pcbnew.LoadBoard({PCB!r})
V = lambda x, y: pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))
n = b.FindNet('GND')
stub = {stub!r}
if stub:
    t = pcbnew.PCB_TRACK(b); t.SetStart(V(*stub)); t.SetEnd(V(*{p!r})); t.SetLayer(pcbnew.F_Cu)
    t.SetWidth(pcbnew.FromMM(0.2)); t.SetNet(n); b.Add(t)
v = pcbnew.PCB_VIA(b); v.SetPosition(V(*{p!r})); v.SetWidth(pcbnew.FromMM({d})); v.SetDrill(pcbnew.FromMM({dr})); v.SetNet(n); b.Add(v)
pcbnew.ZONE_FILLER(b).Fill(b.Zones()); pcbnew.SaveBoard({PCB!r}, b)
'''
    subprocess.run([sys.executable, '-c', code], capture_output=True)


def main():
    spec = json.loads(sys.argv[1])        # [{"cands": [[x,y],...], "stub": [x,y] or null}, ...]
    v0, u0 = drc()
    print('start', v0, u0)
    for s in spec:
        for p in s['cands']:
            shutil.copy(PCB, PCB + '.try')
            add(tuple(p), tuple(s['stub']) if s.get('stub') else None, s.get('d', 0.5), s.get('dr', 0.25))
            v, u = drc()
            if v <= v0 and u < u0:
                print('kept', p, v, u); v0, u0 = v, u
                break
            shutil.copy(PCB + '.try', PCB)
        else:
            print('no candidate worked for', s['cands'][0])


main()
