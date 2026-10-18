"""LCSC cart upload list (LCSC "BOM Tool": upload, map the columns, add to cart).

    python3 tools/lcsc_order.py [boards]        (default 3 = one full 48-port set)

Writes jlcpcb/usb-console-server_lcsc_order_<N>boards.csv with one line per LCSC part:
LCSC Part Number, Quantity, Manufacturer Part, Value, Package, Designators.
Also writes ..._through_hole_only.csv: just the hand-soldered connectors, for when JLCPCB assembles
(and sources) the SMD parts. Quantities are per-board count x boards; J2 (the Pi socket) is only needed on the board that
carries the Pi, so it is ordered once per set. LCSC rounds up to each part's minimum order.
"""
import csv
import math
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCH = os.path.join(ROOT, 'usb-console-server.kicad_sch')
ONE_PER_SET = {'J2'}


def main():
    boards = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    sets = math.ceil(boards / 3)
    with tempfile.TemporaryDirectory() as td:
        f = os.path.join(td, 'b.csv')
        subprocess.run(['kicad-cli', 'sch', 'export', 'bom', '--fields',
                        'Reference,Value,Footprint,LCSC,MPN,${DNP},${EXCLUDE_FROM_BOM}',
                        '--labels', 'Ref,Value,Footprint,LCSC,MPN,DNP,EXCL', '--group-by', '',
                        '--ref-range-delimiter', '', '-o', f, SCH], check=True, capture_output=True)
        rows = list(csv.DictReader(open(f)))
    parts, missing = {}, []
    for r in rows:
        if r['DNP'].strip() or r['EXCL'].strip():
            continue
        lc = r['LCSC'].strip()
        if not lc:
            missing.append(f"{r['Ref']} ({r['Value']})")
            continue
        p = parts.setdefault(lc, {'refs': [], 'value': r['Value'], 'mpn': r['MPN'].strip(),
                                  'fp': r['Footprint'].split(':')[-1], 'qty': 0})
        p['refs'].append(r['Ref'])
        p['qty'] += sets if r['Ref'] in ONE_PER_SET else boards
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from jlc_export import HAND_SOLDER
    for suffix, sel in (('', parts),
                        ('_through_hole_only', {k: v for k, v in parts.items()
                                                if any(r in HAND_SOLDER for r in v['refs'])})):
        out = os.path.join(ROOT, 'jlcpcb', f'usb-console-server_lcsc_order_{boards}boards{suffix}.csv')
        with open(out, 'w', newline='') as fo:
            w = csv.writer(fo)
            w.writerow(['LCSC Part Number', 'Quantity', 'Manufacturer Part', 'Value', 'Package', 'Designators'])
            for lc, p in sorted(sel.items(), key=lambda kv: -kv[1]['qty']):
                w.writerow([lc, p['qty'], p['mpn'], p['value'], p['fp'], ','.join(p['refs'])])
        print(f'{os.path.basename(out)}: {len(sel)} parts, {sum(p["qty"] for p in sel.values())} pieces for {boards} boards')
    if missing:
        print('no LCSC number (buy separately):', ', '.join(missing))


if __name__ == '__main__':
    main()
