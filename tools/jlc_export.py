"""JLCPCB fabrication + assembly package.

    python3 tools/jlc_export.py

Writes jlcpcb/:
  usb-console-server_gerbers.zip   Gerbers + Excellon drill files (upload as the PCB)
  usb-console-server_bom_jlc.csv   Comment, Designator, Footprint, LCSC Part #   (assembly BOM)
  usb-console-server_cpl_jlc.csv   Designator, Mid X, Mid Y, Layer, Rotation     (pick-and-place)

Fields (value, LCSC number, DNP, exclude-from-BOM) come from the schematic; positions from the board.
Parts left out of assembly: DNP, excluded from BOM (test points, mounting holes), and HAND_SOLDER.
"""
import csv
import os
import subprocess
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = 'usb-console-server'
PCB = os.path.join(ROOT, NAME + '.kicad_pcb')
SCH = os.path.join(ROOT, NAME + '.kicad_sch')
OUT = os.path.join(ROOT, 'jlcpcb')
# soldered by hand: the through-hole connectors (Pi socket, power chain, RJ45 block, link)
# and the 3-pin debug headers (Pi console, UPDI)
HAND_SOLDER = {'J2', 'J3', 'J4', 'J5', 'J6', 'J7', 'J8', 'J9'}
# schematic LCSC number -> JLCPCB Basic part with the same spec. The schematic carries parts that LCSC
# itself has in stock (Oct 2026); JLCPCB's own warehouse still stocks these Basic equivalents, which
# avoids the per-type Extended-part fee at assembly.
JLC_BASIC = {
    'C111134': 'C25819',   # 47k 0603 1%
    'C22548': 'C21190',    # 1k 0603 1%
    'C91185': 'C28323',    # 1uF 50V X7R 0805
    'C77091': 'C12891',    # 22uF 25V X5R 1206
}
LAYERS = 'F.Cu,In1.Cu,In2.Cu,B.Cu,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,Edge.Cuts'


def run(*cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def gerbers(td):
    g = os.path.join(td, 'gerbers')
    os.makedirs(g)
    run('kicad-cli', 'pcb', 'export', 'gerbers', '--layers', LAYERS, '--subtract-soldermask',
        '--no-netlist', '-o', g + '/', PCB)
    run('kicad-cli', 'pcb', 'export', 'drill', '--format', 'excellon', '--excellon-units', 'mm',
        '--excellon-separate-th', '--generate-map', '--map-format', 'gerberx2', '-o', g + '/', PCB)
    z = os.path.join(OUT, NAME + '_gerbers.zip')
    with zipfile.ZipFile(z, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(os.listdir(g)):
            zf.write(os.path.join(g, f), f)
    return sorted(os.listdir(g))


def schematic_parts(td):
    f = os.path.join(td, 'bom.csv')
    run('kicad-cli', 'sch', 'export', 'bom', '--fields', 'Reference,Value,Footprint,LCSC,${DNP},${EXCLUDE_FROM_BOM}',
        '--labels', 'Ref,Value,Footprint,LCSC,DNP,EXCL', '--group-by', '', '--ref-range-delimiter', '',
        '-o', f, SCH)
    return {r['Ref']: r for r in csv.DictReader(open(f))}


def main():
    os.makedirs(OUT, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        files = gerbers(td)
        parts = schematic_parts(td)
        pos = os.path.join(td, 'pos.csv')
        run('kicad-cli', 'pcb', 'export', 'pos', '--format', 'csv', '--units', 'mm', '--side', 'both',
            '--exclude-dnp', '-o', pos, PCB)
        placed = list(csv.DictReader(open(pos)))

    fitted, skipped, missing = {}, [], []
    for p in placed:
        ref = p['Ref']
        s = parts.get(ref)
        if s is None or s['DNP'].strip() or s['EXCL'].strip() or ref in HAND_SOLDER:
            skipped.append(ref)
            continue
        if not s['LCSC'].strip():
            missing.append(ref)
            continue
        fitted[ref] = (p, s)

    groups = {}
    for ref, (p, s) in fitted.items():
        lc = s['LCSC'].strip()
        key = (JLC_BASIC.get(lc, lc), s['Value'], s['Footprint'].split(':')[-1])
        groups.setdefault(key, []).append(ref)

    def refkey(r):
        i = len(r.rstrip('0123456789'))
        return (r[:i], int(r[i:] or 0))

    with open(os.path.join(OUT, NAME + '_bom_jlc.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['Comment', 'Designator', 'Footprint', 'LCSC Part #'])
        for (lcsc, val, fp), refs in sorted(groups.items(), key=lambda kv: refkey(min(kv[1], key=refkey))):
            w.writerow([val, ','.join(sorted(refs, key=refkey)), fp, lcsc])

    with open(os.path.join(OUT, NAME + '_cpl_jlc.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['Designator', 'Mid X', 'Mid Y', 'Layer', 'Rotation'])
        for ref in sorted(fitted, key=refkey):
            p, _ = fitted[ref]
            w.writerow([ref, f"{float(p['PosX']):.4f}mm", f"{float(p['PosY']):.4f}mm",
                        'Top' if p['Side'].lower().startswith('top') else 'Bottom', f"{float(p['Rot']):.1f}"])

    print(f'gerber zip: {len(files)} files')
    print(f'BOM: {len(groups)} lines, {len(fitted)} placements')
    print(f'not assembled: {len(skipped)} ({", ".join(sorted(skipped, key=refkey))})')
    if missing:
        print('NO LCSC NUMBER:', ', '.join(missing))


if __name__ == '__main__':
    main()
