# USB Console Server — KiCad 10 project

48 RS-232 console ports on one Raspberry Pi: three identical boards, 16 ports each.
Built from the design record of 2026-10-10. KiCad 10.0.7.

| | State |
|---|---|
| Schematic | Complete, 7 sheets (FT4232H cluster reused 4×). ERC: **0 violations** at all severities. |
| Netlist | Checked pin-by-pin against the design intent (`tools/verify_netlist.py`): 236 nets, 0 mismatches. |
| PCB | **138 × 141 mm**, 4 layers, all 320 footprints placed, **fully routed**, ~3000 track segments, ~1080 vias. USB pairs coupled and length-matched (below). |
| PCB DRC | **0 errors, 0 unconnected**, 0 schematic-parity issues. Warnings: silkscreen overlaps only. |
| BOM | `bom/usb-console-server_bom.csv`. Every line has an LCSC number. |

## Sheets

| Sheet | Contents |
|---|---|
| `power` | 24 V input protection (F2 → Q2 → D3 → C46), board-to-board power J3/J4, 5 V and 3.3 V bucks, LM317 MCU rail |
| `pi` | 2×20 Pi socket, LM74700 ideal diode + Q1, PI_5V bulk + TVS, HAT ID EEPROM, Pi console SP3232 + J6 |
| `hub` | USB-C, PRTR5V0U2F (inline), USB2514BI in I²C-EEPROM mode, 24 MHz crystal, 24LC02B |
| `ftdi` (×4: FT1–FT4) | FT4232HL, 12 MHz crystal, 93LC46B, two MAX3232E → 4 RS-232 ports |
| `ports` | One HC-RJ45-059D 2×8 block (16 units + shell), shield 0R / 1M / C option |
| `mcu` | ATtiny3216, ID straps, rail dividers, JP2, status LED, UPDI, inter-board signal link J8/J9 |

Reference designators the record names are kept: F2, Q1, Q2, R9, R28, R34, R43, D3, D4, C24, C46, C62, C63, C174, X1, JP2, U10–U17.

## Decisions and departures from the record — please review

1. **RJ45 quantity.** LCSC C46261623 is a single 2×8 ganged block with all 16 jacks (~115 mm wide), not a 2-jack block. The BOM has **1 per board**, not 8.
2. **Buck converters** (the record leaves them open). **TPS54560B** (+5V, 5 A) and **TPS54360B** (+3.3V, 3.5 A): both 60 V-rated (above the 42.1 V TVS clamp), with Eco-mode pulse skipping, so they are stable unloaded on boards 2 and 3. Both run at 400 kHz, with EN left open (internal pull-up).
   **Compensation (16.9k / 4.7 nF / 47 pF) is a starting point. Re-derive it in TI WEBENCH** for the chosen L and Cout.
   Inductors are TAI-TECH TMPC1265HP-100MG-D (5 V, Isat 15.5 A; SRP1265A-100M equivalent, same 13.5 × 12.5 mm body) and SRP7028A-100M (3.3 V, Isat 6 A). Output caps are 22 µF/25 V 1206 (a JLCPCB Basic part): 4 on +5V, 3 on +3.3V.
   The LMR33630 was rejected: its 36 V rating is below the clamp.
3. **USB2514B CRFILT/PLLFILT.** The record says 0.01 µF + 0.1 µF. The datasheet asks for ≥1.0 µF low-ESR on each. **Fitted 1 µF + 100 nF. Confirm.**
4. **LM74700 VCAP** goes to ANODE (one of the record's open items; resolved per the datasheet).
5. **Pi mounting: face down on top of the board** (your choice, option B).
   - The Pi sits above our board on four M2.5 × 11 mm standoffs (H5–H8), upside down: its own GPIO header plugs down into J2, a 2×20 socket on our top side.
   - It is turned 90° so the USB/Ethernet block hangs past our top edge. The side edges stay free for the chain, so the Pi board can sit anywhere in it.
   - The Pi's underside faces up, so the **microSD card is reachable without unbolting anything**. It sits at the end toward the FTDI clusters.
   - The builder checks that J2 pad *n* sits under Pi GPIO pin *n* (the face-down Pi is a mirror image of its own top view).
   - The Pi's components face down into the 11 mm gap. **Only parts ≤ 3 mm tall sit under it**, and the builder checks this. The Pi 4 PoE-header spot (approximate, on User.Drawings) is kept clear.
   - The Pi outline and jack block are drawn on User.Drawings and F.Fab.
   - The cabinet needs ~23 mm of clearance beyond board 1's top edge for the jacks and cables.
6. **Board-to-board connectors.** With KiCad's stock right-angle header (J3/J8, right edge) and socket (J4/J9, left edge), pin *n* lands at the same height on both footprints. So the numbering runs the same direction, unlike the record's note, which describes its own footprints. The builder asserts this.
7. **RJ45 port mapping.** Column *j* (0–7, left to right) is P(2j+1) = pads 24j+1..8 and P(2j+2) = pads 24j+9..16. Pads 1/16 sit at the same end, matching the record. **Which physical jack (upper or lower) carries pads 1–8 is not in the vendor data.** The 3–6 loopback at bring-up settles it.
8. **Added parts** the record didn't specify:
   - RESET_REQ: 100k pull-down, for a defined level on boards without a Pi.
   - HAT EEPROM: ID_SD/ID_SC 3.9k pull-ups, plus a WP pull-up and write-enable jumper.
   - VBUS_DET: 47k/100k divider.
   - USB shield: 1M ‖ 4.7 nF to GND.
   - Hub RESET_N: 10k pull-up and 100 nF.
   - Hub LOCAL_PWR: 10k pull-up.
9. **Net classes:**
   - `USB_90R` (0.16 mm width / 0.15 mm gap) assumes JLCPCB's JLC04161H-3313 stackup. Confirm it in their impedance calculator.
   - `HV_24V`: 0.8 mm width, 0.3 mm clearance.
   - `Power`: 0.6 mm width.
10. **USB port swap (PRTSP) — must be set in the hub EEPROM.** On the hub and on the FT4232Hs, D− sits above D+ on the faces that point at each other, so a coupled pair would have to cross over. Instead, D+/D− are swapped at the hub's downstream pins in the schematic (pin 1 = D+ of port 1, pin 2 = D−, and so on for pins 1–4 and 6–9).
    - The USB2514B undoes the swap internally when **PRTSP (register 0xFA) = 0x1E** (ports 1–4) in the 24LC02B image.
    - **Without it, none of the FT4232Hs will enumerate.**
    - The upstream port is not swapped (bit 0 stays 0).

## Board layout (placement only)

138 mm wide: the 115 mm RJ45 block plus a slotted M3 chassis hole in each bottom corner beside it.

- **Bottom edge.** The RJ45 block, front flush with the edge. Silkscreen `P02 / P01`, etc. above each column; slots H2/H3 at its ends.
- **Middle band.** FT1–FT4 clusters, each over its own two jack columns (FT1 at the P01 end). The MAX3232E row sits next to the jacks, then the FT4232H with its crystal and decoupling, then the EEPROM.
- **Edge strips beside the clusters.** Board-to-board connectors: J8 (link) and J3 (24 V) headers on the right, J9/J4 sockets on the left, same heights.
- **Top right.** The Raspberry Pi, face down above the board (X 72–128, Y 0–65), with its jacks over the top edge. J2 runs down the right side. Under the Pi: the MCU, Pi console transceiver, HAT EEPROM, ideal diode and LM317 (all low parts). In the strip to its right: the PI_5V electrolytics plus the UPDI (J7) and console (J6) headers.
- **Top left.** USB-C (near the Pi's USB-A ports for a short cable), the hub, both bucks, and the 24 V input protection.
- **Top corners.** Slots H1/H4.

## Test points

26 bare pads (no BOM cost), all reachable with the Pi fitted. The board builder keeps them out from under the Pi.

| Where | Test points |
|---|---|
| Rails | +24V_RAW, +24V, +5V, +3.3V, +3V3_MCU, PI_5V, PI3.3, and each FT4232H's 1.8 V VCORE (×4) |
| Ground | three: by the bucks, by the USB-C input, beside the jacks (scope ground) |
| Buses | PI_SDA / PI_SCL, HUB_SDA / HUB_SCL |
| Control | HUB_MR_N, FTDI_RST_N, RESET_REQ, ALERT, VBUS_UP, MCU spare PA7 |
| Pi console | PITXD / PIRXD (TTL side; the RS-232 side is on J6) |

## Stackup and routing

**Stackup** (JLCPCB 4-layer):
- F.Cu: signals, plus ground pour.
- In1.Cu: solid ground plane.
- In2.Cu: signals, plus a +3.3V pour and a +5V band.
- B.Cu: signals, plus ground pour.

**Rules:** JLCPCB standard 4-layer: 0.127 mm track and space, 0.5/0.25 mm signal vias.

**Routing pipeline:** `tools/route.py`, then `tools/close_gaps.py`.

1. **Pre-routing** (locked):
   - every FT4232H ground pin gets an inward stub and a via to the ground plane;
   - the four hub→FT4232H USB pairs are routed first as coupled pairs (`tools/diffpair.py`, 0.16 mm / 0.15 mm gap), see "USB pairs" below.

   This stops anything else boxing those 0.5 mm-pitch pins in.
2. **Freerouting 2.4** routes everything else, with these net-class widths:

   | Net class | Width | Nets |
   |---|---|---|
   | USB_90R | 0.16 mm | USB pairs |
   | Power | 0.3 mm | power nets |
   | HV_24V | 0.5 mm | 24 V |
   | Chain24V | 1.0 mm | the 24 V pass-through between J3 and J4 |
   | HighCurrent | 0.6 mm | 5 V path and switch nodes |

3. **Power copper** carries the real current; the traces only guarantee connectivity:
   - pours on both buck switch nodes, both buck inputs, the 5 V buck output and the ideal-diode input;
   - a 7 mm-wide +5V band on In2 from the buck to the Pi, stitched with vias at both ends.
4. **Ground stitching:** a 4 mm via grid across the board, and a via grid under each FT4232H and the hub.
5. **Cleanup** (`close_gaps.py`), driven by KiCad's own DRC report:
   - joins the autorouter's near misses;
   - gives stranded ground or +3.3V pads a via to their plane or pour;
   - maze-routes anything still open, ripping up the nets that wall it in if needed;
   - removes any via that violates clearance.

**USB pairs (hub → FT4232H), routed as coupled pairs:**

| Port | D+ | D− | Skew | Layers |
|---|---|---|---|---|
| 1 | 94.4 mm | 93.3 mm | 1.1 mm | F.Cu only, over the In1 ground plane |
| 2 | 73.7 mm | 72.4 mm | 1.2 mm | F → B.Cu (70 mm) → F, 2 vias per line |
| 3 | 98.6 mm | 99.7 mm | 1.1 mm | F → B.Cu (93 mm) → F, 2 vias per line |
| 4 | 100.7 mm | 98.8 mm | 1.9 mm | F.Cu only |
| upstream (USB-C → hub) | 29.6 mm | 30.7 mm | 1.1 mm | F.Cu |

- 1–2 mm of skew is ≈ 7–13 ps; USB 2.0 high speed tolerates far more, so no tuning meanders were added.
- Where a pair runs on B.Cu, the layer above it (In2) carries a 2.4 mm-wide **ground strip** under the pair, with a no-track keepout and ground vias beside the pair every 4 mm, so B.Cu runs have an unbroken reference too.
- The pair router keeps B.Cu runs away from the hub and FT4232H bodies so their other pins can still escape on In2.
- To make room, **FB1, C43 and C44** (hub VDDA33 filter) were moved ~2 mm out of the hub's USB corridor.
- This depends on the **PRTSP = 0x1E** hub setting (decision 10).
- Previous board (uncoupled pairs): `backups/usb-console-server_saved-edits_20261011-0957.kicad_pcb`.

**Review before ordering:**
- **USB pair impedance:** 0.16 mm / 0.15 mm gap is ≈ 90 Ω differential on JLC04161H-3313 for the F.Cu runs. The B.Cu runs reference the thin In2 strip; confirm both in JLCPCB's impedance calculator.
- **Buck loops:** both are compact pours, but check the input-cap / switch / diode loop of each buck against the TPS54x60 layout guide.
- **Autorouted traces:** the routing is generated, so expect some odd detours. Re-run DRC after any hand edit.

## 3D model

- `3d/usb-console-server.step`: the whole board with every part (16 MB), for enclosure design in any CAD tool. `3d/preview.png` is a quick render.
- Rebuild it in KiCad (File > Export > STEP) or with `kicad-cli pcb export step --subst-models usb-console-server.kicad_pcb`; KiCad's standard 3D library must be installed.
- Models that are not in KiCad's library are in `lib/console_server.3dshapes`:
  - **J5, the 16-port RJ45 block, is a simplified envelope** (`tools/rj45_model.py`): no vendor model or drawing exists. The 115 × 28.2 mm outline and port positions match the footprint, but the **25.4 mm height is assumed** (typical for stacked 2×N RJ45). Measure a real part before finalising an enclosure.
  - L2 (SRP7028A) and J1 (HRO TYPE-C-31-M-12 USB-C) use the EasyEDA models for their LCSC parts.
  - `tools/fix_3d_models.py` points L2, J1, U8 and U9 at these files (KiCad 10 renamed or lacks the stock ones).
- The Raspberry Pi is not in the model. It sits face-down on J2.

## Ordering from JLCPCB

`python3 tools/jlc_export.py` writes `jlcpcb/`:

| File | Upload as |
|---|---|
| `usb-console-server_gerbers.zip` | the PCB: 4 layers, 138 × 141 mm, 1.6 mm, impedance control with stackup **JLC04161H-3313** |
| `usb-console-server_bom_jlc.csv` | assembly BOM: 61 lines, every one with an LCSC number (SMD only) |
| `usb-console-server_cpl_jlc.csv` | pick-and-place: 271 parts, all on the top side |
| `usb-console-server_hand_solder.csv` | not uploaded: the through-hole connectors you solder yourself, with LCSC numbers to buy them |
| `usb-console-server_lcsc_order_3boards.csv` | LCSC BOM Tool upload: **every** part for a full set of 3 boards (to build them all yourself) |
| `usb-console-server_lcsc_order_3boards_through_hole_only.csv` | LCSC BOM Tool upload: only the hand-soldered connectors, when JLCPCB assembles the SMD parts |

- Other board counts: `python3 tools/lcsc_order.py <boards>`. LCSC rounds each line up to its minimum order quantity.
- **Not assembled by JLC:** test points, mounting holes and DNP parts, and every through-hole connector (J2 Pi socket, J3/J4 power chain, J5 RJ45 block, J8/J9 link, J6/J7 debug headers). These are soldered by hand; see `usb-console-server_hand_solder.csv`.
- **Check every part's rotation in JLC's placement preview.** KiCad and JLC disagree on the zero orientation of some packages (often SOT-223, SOT-23-6, SOIC, the LQFP/QFN ICs, diodes, electrolytics, crystals). Fix any that look wrong in their editor before paying.
- Stock and "extended part" fees change daily; JLC flags missing parts when you upload the BOM.

## Regenerating

Everything is generated from `tools/design.py` (schematic) and `tools/build_pcb.py` (board), which need KiCad 10 with its Python module. `tools/build_all.sh` rebuilds and re-verifies the whole project.

**Rebuilding overwrites the board placement.** Once you start editing the PCB by hand, treat the KiCad files as the source and stop regenerating.

## Bring-up checklist (from the record)

1. 24 V with no load, then reverse polarity: nothing downstream should see voltage.
2. All three rails unloaded; the 5 V buck must be stable at no load.
3. MCU alone: UPDI, ID straps read with a meter, heartbeat.
4. `i2cdetect -y 1` shows 0x30.
5. Hub EEPROM (**including PRTSP = 0x1E**), `lsusb`.
6. One FT4232H, then its EEPROM serial (`HUB<b>-P<n>`).
7. Loopback (pin 3 ↔ pin 6) on every port, including one upper and one lower jack early.
8. udev rules.
9. Chain boards 2 and 3.
