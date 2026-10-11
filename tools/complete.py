"""Freerouting completion pass for what is still wrong after route.py + close_gaps.py.

Rips only the problem nets (those with unconnected items, and those with tracks inside a
no-track keepout), locks everything else, lets Freerouting reroute just those nets around
the locked copper (it pushes and shoves far better than the maze fallback), imports the
result and unlocks again (the USB pairs and pre-fanout stay locked).

Run on a saved, routed board:  python3 tools/complete.py [passes]
"""
import json
import os
import re
import subprocess
import sys
import tempfile

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from close_gaps import PCB, strip_tracks  # noqa: E402
import route  # noqa: E402

KEEP_LOCKED = re.compile(r'USB_DN\d_D[PM]')


def problem_nets():
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, 'd.json')
        subprocess.run(['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-error', '-o', out, PCB],
                       capture_output=True)
        d = json.load(open(out))
    nets = set()
    for u in d['unconnected_items']:
        for it in u['items']:
            m = re.search(r'\[([^\]]+)\]', it['description'])
            if m and m.group(1) != 'GND':
                nets.add(m.group(1))
    for v in d['violations']:
        if v['type'] in ('items_not_allowed', 'clearance', 'shorting_items', 'tracks_crossing'):
            for it in v['items']:
                m = re.search(r'^(?:Track|Via) \[([^\]]+)\]', it['description'])
                if m and m.group(1) not in ('GND',) and not KEEP_LOCKED.match(m.group(1)):
                    nets.add(m.group(1))
    return sorted(nets), d


def main():
    passes = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    nets, _ = problem_nets()
    print('problem nets:', ', '.join(nets) or 'none')
    if not nets:
        return
    n = strip_tracks(PCB, nets)
    print(f'ripped {n} tracks/vias of those nets')
    b = pcbnew.LoadBoard(PCB)
    was_locked = {t.m_Uuid.AsString() for t in b.GetTracks() if t.IsLocked()}
    for t in b.GetTracks():
        t.SetLocked(True)
    pcbnew.SaveBoard(PCB, b)
    b = pcbnew.LoadBoard(PCB)
    work = os.path.join(os.path.dirname(PCB), 'route')
    os.makedirs(work, exist_ok=True)
    dsn, ses = os.path.join(work, 'complete.dsn'), os.path.join(work, 'complete.ses')
    pcbnew.ExportSpecctraDSN(b, dsn)
    cmd = [route.JAVA, '-jar', route.JAR, '--gui.enabled=false', '-de', dsn, '-do', ses, '-mp', str(passes),
           '-mt', str(max(1, (os.cpu_count() or 2) - 1))]
    with open(os.path.join(work, 'complete.log'), 'w') as log:
        subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True)
    if not pcbnew.ImportSpecctraSES(b, ses):
        raise SystemExit('SES import failed')
    for t in b.GetTracks():
        t.SetLocked(t.m_Uuid.AsString() in was_locked or KEEP_LOCKED.match(t.GetNetname()) is not None)
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    pcbnew.SaveBoard(PCB, b)
    nets2, d = problem_nets()
    print(f'after completion: {len(d["unconnected_items"])} unconnected, {len(d["violations"])} violations; '
          f'problem nets left: {", ".join(nets2) or "none"}')


if __name__ == '__main__':
    main()
