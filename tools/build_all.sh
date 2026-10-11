#!/bin/bash
# Regenerate the whole project from tools/design.py and verify it.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 tools/footprints.py >/dev/null
python3 tools/verify_netlist.py                       # writes the schematic, checks every net
for f in *.kicad_sch; do kicad-cli sch upgrade "$f" >/dev/null 2>&1; done   # canonical KiCad 10 format
kicad-cli sch erc --severity-all --exit-code-violations -o /tmp/ucs_erc.rpt usb-console-server.kicad_sch 2>/dev/null | grep -i found || true
python3 tools/build_pcb.py 2>/dev/null
FR_STRATEGY=${FR_STRATEGY:-hybrid} python3 tools/route.py 40 2>/dev/null | grep -vE "Debug|memory leak"   # ~10 min
python3 tools/close_gaps.py 2>/dev/null | grep -vE "Debug|memory leak|island on"
kicad-cli pcb drc --schematic-parity --severity-error -o /tmp/ucs_drc.rpt usb-console-server.kicad_pcb 2>/dev/null | grep -i found || true
# NOTE: routing overwrites the board. Once you edit copper by hand in KiCad, stop running this script.
mkdir -p bom
kicad-cli sch export bom --fields 'Reference,Value,Footprint,LCSC,Spec,MPN,Note,${QUANTITY},${DNP}' \
  --labels 'Designator,Value,Footprint,LCSC,Spec,MPN,Note,Qty,DNP' --group-by 'Value,Footprint,LCSC,${DNP}' \
  --ref-range-delimiter '' --exclude-dnp -o bom/usb-console-server_bom.csv usb-console-server.kicad_sch >/dev/null 2>&1
echo "BOM lines: $(($(wc -l < bom/usb-console-server_bom.csv) - 1))"
