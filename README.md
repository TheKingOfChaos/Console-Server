# USB Console Server — KiCad 10 project

48 RS-232 console ports on one Raspberry Pi: three identical boards, 16 ports each.
Built from the design record of 2026-10-10. KiCad 10.0.7. **This is Rev B** (git tag `rev-A` is the first board).

| | State |
|---|---|
| Schematic | Complete, 7 sheets (FT4232H cluster reused 4×). ERC: **0 violations** at all severities. |
| Netlist | Checked pin-by-pin against the design intent (`tools/verify_netlist.py`): 255 nets, 0 mismatches. |
| PCB | **150 × 155 mm**, 4 layers, all 361 footprints placed, **fully routed**, ~3330 track segments, ~1170 vias. USB pairs coupled, length-matched and all on F.Cu (below). |
| PCB DRC | **0 errors, 0 unconnected**, 0 schematic-parity issues. Warnings: silkscreen overlaps only. |
| BOM | `bom/usb-console-server_bom.csv`. Every line has an LCSC number. |

## What changed in Rev B

- **Bring up each section on its own.** Every section gets its power through a **3-pin header: 1 = supply, 2 = section, 3 = GND**:

  | Header | Section | Supply |
  |---|---|---|
  | J11 | 5 V buck (input side) | +24V |
  | J12 | 3.3 V buck (input side) | +24V |
  | J13 | USB hub, its EEPROM and crystal (+3V3_HUB) | +3.3V |
  | J14–J17 | FT1–FT4 clusters: FT4232H, EEPROM, both MAX3232E (VCC3) | +3.3V |

  - **Jumper on 1–2** = normal running (7 jumper caps per board, LCSC C100114).
  - **Jumper off**: the section is unpowered. Put an ammeter across 1–2 to read its current, or feed it from a bench supply on 2–3.
  - A jumper put on 2–3 by mistake only grounds the dead section; it cannot short a supply.
  - The Pi needs no header: it is simply not plugged in for early bring-up.
- **Power LEDs** (green, ~1 mA) on +24V, +5V, +3.3V, +3V3_MCU (a labelled column on the left edge), PI_5V and the MCU heartbeat (right of the Pi), the hub (by J13) and each cluster (by its header).
- **Current and voltage sense:** an INA219 behind a shunt on each buck output, 5 mΩ on +5V (U23, I²C 0x40) and 10 mΩ on +3.3V (U24, 0x41). They sit on the MCU's own I²C bus (PA5/PA6, bit-banged, powered from +3V3_MCU), which replaces the old 5 V / 3.3 V ADC dividers. The buck feedback is taken before the shunt.
- **24 V screw terminal J10** (5.08 mm, 2-pin, 18 A) on the left edge, wired to +24V_RAW like the chain connectors: wire up one board, and the chain feeds the rest.
- **MCU UART header J19** (TX PB2 – GND – RX PB3). It shares the SPARE lines of the link, so debug one board at a time.
- **Test points** for each cluster's VCC3 and the INA bus (36 in total).
- **Layout:** the board grew to 150 × 155 mm. The hub moved to the top middle, so the four USB pairs drop straight down to their clusters, all on F.Cu with no vias. Each buck's power stage (inductor, catch diode, output caps, shunt, INA219) is now placed by hand: in Rev A the autoplacer had put the output caps 70 mm from the inductors.

## Sheets

| Sheet | Contents |
|---|---|
| `power` | 24 V terminal J10, input protection (F2 → Q2 → D3 → C46), board-to-board power J3/J4, 5 V and 3.3 V bucks behind bring-up headers J11/J12, shunts + INA219s, rail LEDs, LM317 MCU rail |
| `pi` | 2×20 Pi socket, LM74700 ideal diode + Q1, PI_5V bulk + TVS, HAT ID EEPROM, Pi console SP3232 + J6 |
| `hub` | USB-C, PRTR5V0U2F (inline), USB2514BI in I²C-EEPROM mode, 24 MHz crystal, 24LC02B |
| `ftdi` (×4: FT1–FT4) | FT4232HL, 12 MHz crystal, 93LC46B, two MAX3232E → 4 RS-232 ports |
| `ports` | One HC-RJ45-059D 2×8 block (16 units + shell), shield 0R / 1M / C option |
| `mcu` | ATtiny3216, ID straps, 24 V divider, JP2, INA bus pull-ups, status LED, UPDI, UART header J19, inter-board signal link J8/J9 |

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

## Board layout

150 × 155 mm: the 115 mm RJ45 block along the bottom, a slotted M3 chassis hole in each corner.

- **Bottom edge.** The RJ45 block, front flush with the edge. Silkscreen `P02 / P01`, etc. above each column.
- **Middle band.** FT1–FT4 clusters, each over its own two jack columns (FT1 at the P01 end): MAX3232E row next to the jacks, then the FT4232H with its crystal and decoupling, then the EEPROM. Above each cluster: its bring-up header (`FT1 3V3` ...) and power LED.
- **Edge strips beside the clusters.** Board-to-board connectors: J8 (link) and J3 (24 V) headers on the right, J9/J4 sockets on the left, same heights. J10, the 24 V screw terminal, sits above J9 with its wire openings at the left edge.
- **Top left: power.** J11/J12 bring-up headers and the rail LEDs on the left edge; the 5 V buck (U8, L1, D1, output caps, shunt R10, INA219 U23) and below it the 3.3 V buck (U9, L2, D2, caps, R11, U24); the 24 V protection (F2, Q2, C46) next to the terminal.
- **Top middle: USB.** USB-C J1 on the top edge near the Pi's USB-A ports, the PRTR5V0U2F, the hub U5 with its crystal and EEPROM, J13 (hub bring-up header). A clear corridor below the hub carries the four USB pairs to the clusters.
- **Top right.** The Raspberry Pi, face down above the board (X 84–140, Y 0–65), with its jacks over the top edge; J2 runs down its right side. Under the Pi: the MCU, Pi console transceiver, HAT EEPROM, ideal diode and LM317 (all low parts). In the 10 mm strip right of it: UPDI J7, Pi console J6, MCU UART J19, the PI_5V electrolytics and the MCU / PI5V LEDs.

## Test points

36 bare pads (no BOM cost), all reachable with the Pi fitted. The board builder keeps them out from under the Pi.

| Where | Test points |
|---|---|
| Rails | +24V_RAW, +24V, +5V, +3.3V, +3V3_MCU, PI_5V, PI3.3, and per cluster its VCC3 and 1.8 V VCORE (×4) |
| Ground | by the bucks, by the USB-C input, beside the jacks (scope ground), and one per cluster |
| Buses | PI_SDA / PI_SCL, HUB_SDA / HUB_SCL, INA_SDA / INA_SCL |
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
   - pours on both buck switch nodes, both buck inputs, both buck outputs (up to the shunts) and the ideal-diode input;
   - a 7 mm-wide +5V band on In2 from the 5 V shunt to the Pi, stitched with vias at both ends;
   - an In2 pour per switched section (+3V3_HUB, each cluster's VCC3) over that section.
4. **Ground stitching:** a 4 mm via grid across the board, and a via grid under each FT4232H and the hub.
5. **Cleanup** (`close_gaps.py`), driven by KiCad's own DRC report:
   - joins the autorouter's near misses;
   - gives stranded ground or +3.3V pads a via to their plane or pour;
   - maze-routes anything still open, ripping up the nets that wall it in if needed;
   - removes any via that violates clearance.

**USB pairs (hub → FT4232H), routed as coupled pairs, all on F.Cu over the In1 ground plane, no vias:**

| Port | D+ | D− | Skew |
|---|---|---|---|
| 1 | 97.4 mm | 96.2 mm | 1.1 mm |
| 2 | 82.4 mm | 81.2 mm | 1.1 mm |
| 3 | 83.2 mm | 82.1 mm | 1.1 mm |
| 4 | 89.4 mm | 87.5 mm | 1.9 mm |
| upstream (USB-C → hub) | 17.1 mm | 18.0 mm | 0.9 mm |

- 1–2 mm of skew is ≈ 7–13 ps; USB 2.0 high speed tolerates far more, so no tuning meanders were added.
- The pair router keeps the pairs clear of the bucks' whole power stage and never routes under an IC body.
- This depends on the **PRTSP = 0x1E** hub setting (decision 10).
- Hand fixes after the generated routing (so `build_all.sh` alone does not reproduce this board exactly): FT1's VPHY escapes through a 0.45 mm via at pin 4 (its VCORE B.Cu track was moved 0.55 mm to make room), TP12 moved 0.6 mm, one GND stitching via in an isolated F.Cu pour island.

**Review before ordering:**
- **USB pair impedance:** 0.16 mm / 0.15 mm gap is ≈ 90 Ω differential on JLC04161H-3313 (F.Cu over In1). Confirm it in JLCPCB's impedance calculator.
- **Buck loops:** the power stages are now placed by hand, but still check each input-cap / switch / diode loop against the TPS54x60 layout guide.
- **INA219 shunt routing:** IN+/IN- are routed from the shunt pads by the autorouter; check they are Kelvin connections (from the shunt pads themselves, not from the pour).
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
| `usb-console-server_gerbers.zip` | the PCB: 4 layers, 150 × 155 mm, 1.6 mm, impedance control with stackup **JLC04161H-3313** |
| `usb-console-server_bom_jlc.csv` | assembly BOM: 65 lines, every one with an LCSC number (SMD only) |
| `usb-console-server_cpl_jlc.csv` | pick-and-place: 293 parts, all on the top side |
| `usb-console-server_hand_solder.csv` | not uploaded: the through-hole connectors you solder yourself, with LCSC numbers to buy them |
| `usb-console-server_lcsc_order_3boards.csv` | LCSC BOM Tool upload: **every** part for a full set of 3 boards (to build them all yourself) |
| `usb-console-server_lcsc_order_3boards_through_hole_only.csv` | LCSC BOM Tool upload: only the hand-soldered connectors and jumper caps, when JLCPCB assembles the SMD parts |
| `..._5boards.csv`, `..._5boards_through_hole_only.csv` | the same for 5 boards |

- Other board counts: `python3 tools/lcsc_order.py <boards>`. LCSC rounds each line up to its minimum order quantity.
- **Not assembled by JLC:** test points, mounting holes and DNP parts, and every through-hole part (J2 Pi socket, J3/J4 power chain, J5 RJ45 block, J8/J9 link, J6/J7/J19 debug headers, J10 terminal, J11–J17 bring-up headers) plus the 7 jumper caps. These are soldered by hand; see `usb-console-server_hand_solder.csv`.
- **Check every part's rotation in JLC's placement preview.** KiCad and JLC disagree on the zero orientation of some packages (often SOT-223, SOT-23-6, SOIC, the LQFP/QFN ICs, diodes, electrolytics, crystals). Fix any that look wrong in their editor before paying.
- Stock and "extended part" fees change daily; JLC flags missing parts when you upload the BOM.

## Regenerating

Everything is generated from `tools/design.py` (schematic) and `tools/build_pcb.py` (board), which need KiCad 10 with its Python module. `tools/build_all.sh` rebuilds and re-verifies the whole project.

**Rebuilding overwrites the board placement.** Once you start editing the PCB by hand, treat the KiCad files as the source and stop regenerating.

## Bring-up checklist

Start with **every bring-up jumper off** (J11–J17) and no Pi.

1. **24 V input.** 24 V on J10 with a current-limited supply: the 24V and MCU LEDs light, nothing else. Then reverse polarity: nothing downstream may see voltage.
2. **MCU alone** (it runs from its own LM317): UPDI on J7, heartbeat LED, ID straps read with a meter, J19 UART.
3. **5 V buck:** jumper J11. 5V LED on, 5.09 V at TP +5V, stable with no load (it is unloaded on boards 2 and 3 for life). INA219 U23 at 0x40 on the MCU's INA bus.
4. **3.3 V buck:** jumper J12. 3V3 LED on, 3.33 V, INA219 U24 at 0x41.
5. **Hub:** jumper J13 (meter across 1–2 first: expect ~50–100 mA). Program the hub EEPROM (**including PRTSP = 0x1E**), then `lsusb` from a PC shows the hub.
6. **One cluster at a time:** jumper J14 (FT1), check its current on 1–2 (~70–150 mA), `lsusb` shows the FT4232H, program its EEPROM serial (`HUB<b>-P<n>`), then J15–J17.
7. **Pi:** fit it on one board, PI5V LED, `i2cdetect -y 1` shows 0x30.
8. **Loopback** (pin 3 ↔ pin 6) on every port, including one upper and one lower jack early.
9. udev rules.
10. Chain boards 2 and 3.
