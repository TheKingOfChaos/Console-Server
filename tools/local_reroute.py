"""Local Freerouting pass: rip the copper of some nets inside a box, lock everything else,
let Freerouting reconnect just those pieces (it honours the In2 USB keepouts as wire_keepout),
import, restore lock state, refill.

    python3 tools/local_reroute.py X0 Y0 X1 Y1 NET [NET ...]

Only segments/vias of the named nets with an end (or the via) inside the box are ripped,
so long nets such as +3.3V or HUB_SDA keep their copper outside the box.
"""
import os
import subprocess
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from close_gaps import PCB, strip_uuids  # noqa: E402
from complete import KEEP_LOCKED, problem_nets  # noqa: E402
import route  # noqa: E402


def main():
    x0, y0, x1, y1 = map(float, sys.argv[1:5])
    nets = set(sys.argv[5:])

    def inside(p):
        return x0 <= pcbnew.ToMM(p.x) <= x1 and y0 <= pcbnew.ToMM(p.y) <= y1

    b = pcbnew.LoadBoard(PCB)
    rip = {t.m_Uuid.AsString() for t in b.GetTracks()
           if t.GetNetname() in nets and (inside(t.GetStart()) or inside(t.GetEnd()))}
    was_locked = {t.m_Uuid.AsString() for t in b.GetTracks() if t.IsLocked()}
    del b
    print(f'ripping {strip_uuids(PCB, rip)} items in the box')
    b = pcbnew.LoadBoard(PCB)
    for t in b.GetTracks():
        t.SetLocked(True)
    pcbnew.SaveBoard(PCB, b)
    b = pcbnew.LoadBoard(PCB)
    work = os.path.join(os.path.dirname(PCB), 'route')
    dsn, ses = os.path.join(work, 'local.dsn'), os.path.join(work, 'local.ses')
    pcbnew.ExportSpecctraDSN(b, dsn)
    cmd = [route.JAVA, '-jar', route.JAR, '--gui.enabled=false', '-de', dsn, '-do', ses, '-mp', '30',
           '-mt', str(max(1, (os.cpu_count() or 2) - 1))]
    with open(os.path.join(work, 'local.log'), 'w') as log:
        subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True)
    if not pcbnew.ImportSpecctraSES(b, ses):
        raise SystemExit('SES import failed')
    for t in b.GetTracks():
        t.SetLocked(t.m_Uuid.AsString() in was_locked or KEEP_LOCKED.match(t.GetNetname()) is not None)
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    pcbnew.SaveBoard(PCB, b)
    left, d = problem_nets()
    print(f'after local reroute: {len(d["unconnected_items"])} unconnected, {len(d["violations"])} violations; '
          f'problem nets: {", ".join(left) or "none"}')


if __name__ == '__main__':
    main()
