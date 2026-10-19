"""USB Console Server: the design record expressed as a KiCad hierarchical schematic.

Run:  python3 tools/design.py      (from the project directory)
Writes the .kicad_sch files, the project library and the .kicad_pro.
"""
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sexp import Sym, dump
from kicadlib import SymbolLib, build_custom, write_library
from schgen import (Project, Writer, SheetRef, sheet_symbol, allocate_refs, uid, POWER_NETS, _wire, snap)
from parts import LCSC, spec

PROJ = 'usb-console-server'
OUT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FP = dict(
    R='Resistor_SMD:R_0603_1608Metric', C='Capacitor_SMD:C_0603_1608Metric',
    C0805='Capacitor_SMD:C_0805_2012Metric', C1206='Capacitor_SMD:C_1206_3216Metric',
    C1210='Capacitor_SMD:C_1210_3225Metric', FB='Inductor_SMD:L_0603_1608Metric',
    LED='LED_SMD:LED_0603_1608Metric', SOD123='Diode_SMD:D_SOD-123',
)

lib = SymbolLib()
build_custom(lib)
P = Project(PROJ, lib)


def flds(key=None, **extra):
    f = {}
    if key and key in LCSC:
        f['LCSC'] = LCSC[key]
    if key:
        s = spec(key)
        if s:
            f['Spec'] = s
    f.update(extra)
    return f


class Row:
    """A row of vertical two-pin parts. Pin 1 (top) gets net a, pin 2 (bottom) net b."""
    def __init__(self, sh, x, y, dx=10.16):
        self.sh, self.x, self.y, self.dx = sh, x, y, dx

    def _put(self, lib_id, prefix, value, a, b, fp, key, dnp=False, ref=None, **kw):
        p = self.sh.add(lib_id, (self.x, self.y), ref or prefix, value, {'1': a, '2': b}, fp=fp,
                        fields=flds(key, **kw.pop('extra', {})), dnp=dnp, **kw)
        self.x += self.dx
        return p

    def R(self, value, a, b, key=None, fp=FP['R'], **kw):
        return self._put('Device:R', 'R', value, a, b, fp, key or f'R:{value}', **kw)

    def C(self, value, a, b, key=None, fp=FP['C'], **kw):
        return self._put('Device:C', 'C', value, a, b, fp, key or f'C:{value}', **kw)

    def CP(self, value, a, b, key, fp, **kw):
        return self._put('Device:C_Polarized', 'C', value, a, b, fp, key, **kw)

    def FB(self, a, b, **kw):
        return self._put('Device:FerriteBead_Small', 'FB', 'BLM18KG601SN1D', a, b, FP['FB'], 'FB:BLM18KG601SN1D', **kw)

    def L(self, value, a, b, key, fp, **kw):
        return self._put('Device:L', 'L', value, a, b, fp, key, **kw)

    def gap(self, n=1):
        self.x += self.dx * n


def flag(sh, net, x, y):
    return sh.add('power:PWR_FLAG', (x, y), '#FLG', 'PWR_FLAG', {'1': net})


# =====================================================================
# Power: 24 V input protection, inter-board power, 5 V / 3.3 V bucks, MCU LDO
# =====================================================================
pw = P.sheet('power.kicad_sch', 'Power: 24 V input, rails')
pw.text('24 V INPUT PROTECTION\n'
        'Order: fuse -> high-side P-FET (reverse block) -> TVS -> bulk.\n'
        'FET drain faces the supply; nothing in the ground path (I2C between boards references GND).\n'
        'Pass-through to the next board taps +24V_RAW upstream of F2: each board fuses only its own load.\n'
        'SMAJ26A clamps at 42.1 V worst case: below the LM317 40 V in-out limit with 3.3 V out,\n'
        'and inside the 60 V rating of both bucks.', (20, 22))
# inter-board power connectors (4-way, symmetric 24V-GND-GND-24V)
for i, (ref, mpn, fp) in enumerate((
        ('J3', '1x04 R/A pin header', 'Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Horizontal'),
        ('J4', '1x04 R/A socket', 'Connector_PinSocket_2.54mm:PinSocket_1x04_P2.54mm_Horizontal'))):
    pw.add('Connector_Generic:Conn_01x04', (35 + 30 * i, 75), ref, 'PWR_CHAIN',
           {'1': '+24V_RAW', '2': 'GND', '3': 'GND', '4': '+24V_RAW'}, fp=fp,
           fields=flds('J:1x04_hdr' if i == 0 else 'J:1x04_skt', Note=mpn), rot=180)
pw.text('J3 (header, right edge) / J4 (socket, left edge):\n'
        'daisy chain, mate board-to-board. Symmetric pinout:\nreversal maps 24 V to 24 V, GND to GND.', (20, 88), 1.27)
flag(pw, '+24V_RAW', 100, 60)
flag(pw, 'GND', 110, 60)
# fuse, FET, gate clamp
pw.add('Device:Fuse', (130, 70), 'F2', '0468002.NRHF', {'1': '+24V_RAW', '2': '24V_FUSED'},
       fp='Fuse:Fuse_1206_3216Metric', fields=flds('F:0468002', Note='2 A time-lag 63 V DC, I2t 0.506 A2s'))
pw.add('console_server:IRFR5305', (160, 70), 'Q2', 'IRFR5305TRPBF', {'1': 'Q2_GATE', '2': '24V_FUSED', '3': '+24V'},
       rot=90, fp='console_server:TO-252-3_L6.5-W6.1-P4.58-LS9.9-BR',
       fields=flds('Q:IRFR5305', Note='P-ch 55 V 65 mOhm; drain to input'))
r = Row(pw, 150, 100)
r.R('10k', 'Q2_GATE', 'GND', ref='R34')
pw.add('Device:D_Zener', (175, 95), 'D4', 'BZT52C15', {'1': '+24V', '2': 'Q2_GATE'}, rot=90, fp=FP['SOD123'],
       fields=flds('D:BZT52C15', Note='15 V gate clamp, cathode to source'))
pw.add('Diode:SMAJ26A', (195, 95), 'D3', 'SMAJ26A', {'1': '+24V', '2': 'GND'}, rot=90,
       fields=flds('D:SMAJ26A', Note='26 V standoff, 42.1 V max clamp'))
r = Row(pw, 210, 95)
r.CP('100uF', '+24V', 'GND', 'C:100uF63', 'Capacitor_SMD:CP_Elec_10x10.5', ref='C46')
r.C('100nF', '+24V', 'GND')
flag(pw, '+24V', 235, 85)

# 5 V buck, TPS54560B
pw.text('+5V BUCK (Raspberry Pi, 5 A)\nTPS54560B: 60 V, 5 A, Eco-mode pulse skipping at light load:\n'
        'boards 2 and 3 run it unloaded. fsw = 400 kHz (RT 243k).\n'
        'Vout = 0.8 V x (1 + 53.6k/10k) = 5.09 V.\n'
        'COMPENSATION VALUES ARE A STARTING POINT: re-derive in TI WEBENCH for the final L/Cout.', (20, 125))
pw.add('Regulator_Switching:TPS54560BDDA', (70, 175), 'U8', 'TPS54560BDDAR',
       {'1': 'BOOT5', '2': '+24V', '3': None, '4': 'RT5', '5': 'FB5', '6': 'COMP5', '7': 'GND', '8': 'SW5', '9': 'GND'},
       fp='Package_SO:Texas_HSOP-8-1EP_3.9x4.9mm_P1.27mm_ThermalVias', fields=flds('U:TPS54560B'), label_len=5.08)
pw.text('EN left open: internal pull-up enables the part.', (40, 195), 1.27)
r = Row(pw, 20, 215)
r.C('2.2uF', '+24V', 'GND', key='C:2.2uF100', fp=FP['C1210'])
r.C('2.2uF', '+24V', 'GND', key='C:2.2uF100', fp=FP['C1210'])
r.C('100nF', '+24V', 'GND')
r.R('243k', 'RT5', 'GND')
r.R('16.9k', 'COMP5', 'COMP5_RC')
r.C('4.7nF', 'COMP5_RC', 'GND')
r.C('47pF', 'COMP5', 'GND')
r.C('100nF', 'BOOT5', 'SW5')
r.gap()
r.L('10uH', 'SW5', '+5V', 'L:SRP1265A', 'console_server:L_Bourns_SRP1265A', extra={'MPN': 'TMPC1265HP-100MG-D', 'Note': 'Isat 15.5 A, Irms 10 A (SRP1265A-100M equivalent)'})
pw.add('Device:D_Schottky', (r.x + 4, 215), 'D', 'B560C', {'1': 'SW5', '2': 'GND'}, rot=90, fp='Diode_SMD:D_SMC',
       fields=flds('D:B560C'))
r.gap(2)
r.R('53.6k', '+5V', 'FB5')
r.R('10k', 'FB5', 'GND')
for _ in range(4):
    r.C('22uF', '+5V', 'GND', key='C:22uF', fp=FP['C1206'])
flag(pw, '+5V', r.x + 5, 205)

# 3.3 V buck, TPS54360B
pw.text('+3.3V BUCK (4x FT4232H, hub, 8x MAX3232E: ~700 mA)\nTPS54360B: 60 V, 3.5 A, Eco-mode. fsw = 400 kHz.\n'
        'Vout = 0.8 V x (1 + 31.6k/10k) = 3.33 V.', (220, 125))
pw.add('Regulator_Switching:TPS54360DDA', (270, 175), 'U9', 'TPS54360BDDAR',
       {'1': 'BOOT3', '2': '+24V', '3': None, '4': 'RT3', '5': 'FB3', '6': 'COMP3', '7': 'GND', '8': 'SW3', '9': 'GND'},
       fp='Package_SO:TI_SO-PowerPAD-8_ThermalVias', fields=flds('U:TPS54360B'), label_len=5.08)
r = Row(pw, 220, 215)
r.C('2.2uF', '+24V', 'GND', key='C:2.2uF100', fp=FP['C1210'])
r.C('100nF', '+24V', 'GND')
r.R('243k', 'RT3', 'GND')
r.R('16.9k', 'COMP3', 'COMP3_RC')
r.C('4.7nF', 'COMP3_RC', 'GND')
r.C('47pF', 'COMP3', 'GND')
r.C('100nF', 'BOOT3', 'SW3')
r.gap()
r.L('10uH', 'SW3', '+3.3V', 'L:SRP7028A', 'Inductor_SMD:L_Bourns_SRP7028A_7.3x6.6mm', extra={'MPN': 'SRP7028A-100M', 'Note': 'Isat 6 A, Irms 3.5 A'})
pw.add('Device:D_Schottky', (r.x + 4, 215), 'D', 'B360A', {'1': 'SW3', '2': 'GND'}, rot=90, fp='Diode_SMD:D_SMA',
       fields=flds('D:B360A'))
r.gap(2)
r.R('31.6k', '+3.3V', 'FB3')
r.R('10k', 'FB3', 'GND')
for _ in range(3):
    r.C('22uF', '+3.3V', 'GND', key='C:22uF', fp=FP['C1206'])
flag(pw, '+3.3V', r.x + 5, 205)

# MCU rail: LM317 off the protected 24 V
pw.text('+3V3_MCU: independent LM317 off +24V, so the MCU outlives the rails it monitors.\n'
        'R9 = 240R sets 5.2 mA minimum load - do not raise it. Vout = 1.25 x (1 + 390/240) = 3.28 V.\n'
        'Dissipation ~315 mW. SOT-223 tab is the OUTPUT (3.3 V pour), not GND.', (290, 22))
pw.add('Regulator_Linear:LM317_SOT-223', (330, 70), 'U7', 'LM317', {'1': 'LM317_ADJ', '2': '+3V3_MCU', '3': '+24V'},
       fields=flds('U:LM317'))
r = Row(pw, 300, 100)
r.C('1uF', '+24V', 'GND', key='C:1uF50', fp=FP['C0805'])
r.R('240', '+3V3_MCU', 'LM317_ADJ', ref='R9', extra={'Note': '1.25V/240R = 5.2 mA min load; do not raise'})
r.R('390', 'LM317_ADJ', 'GND')
r.C('10uF', '+3V3_MCU', 'GND', key='C:10uF', fp=FP['C0805'])
r.C('100nF', '+3V3_MCU', 'GND')

# =====================================================================
# Raspberry Pi interface
# =====================================================================
pi = P.sheet('pi.kicad_sch', 'Raspberry Pi interface')
pi.text('RASPBERRY PI 40-PIN HEADER  (fit the Pi on ONE board only - nothing in hardware prevents three)\n'
        'Fitted as a HAT-style 2x20 socket: the Pi sits below this board on standoffs, holes on the HAT pattern.\n'
        'PI_SDA/PI_SCL: the Pi has 1.8k pull-ups on GPIO2/3. Board-side pull-ups are DNP - more would\n'
        'drop the effective pull-up too low. RESET_REQ is active-high: GPIO17 defaults to pull-down at boot.', (20, 22))
pi_nets = {str(n): None for n in range(1, 41)}
pi_nets.update({'1': 'PI3.3', '17': 'PI3.3', '2': 'PI_5V', '4': 'PI_5V', '3': 'PI_SDA', '5': 'PI_SCL', '7': 'ALERT',
                '8': 'PITXD', '10': 'PIRXD', '11': 'RESET_REQ', '27': 'ID_SD', '28': 'ID_SC'})
for g in (6, 9, 14, 20, 25, 30, 34, 39):
    pi_nets[str(g)] = 'GND'
pi.add('Connector_Generic:Conn_02x20_Odd_Even', (80, 110), 'J2', 'RPi GPIO 2x20', pi_nets,
       fp='Connector_PinSocket_2.54mm:PinSocket_2x20_P2.54mm_Vertical', fields=flds('J:2x20_skt'), label_len=5.08)
flag(pi, 'PI3.3', 40, 150)
r = Row(pi, 25, 160)
r.R('4.7k', 'PI3.3', 'PI_SDA', dnp=True, extra={'Note': 'DNP - Pi has 1.8k on board'})
r.R('4.7k', 'PI3.3', 'PI_SCL', dnp=True, extra={'Note': 'DNP - Pi has 1.8k on board'})

# ideal diode
pi.text('IDEAL DIODE: LM74700-Q1 + AON7524 (30 V, 3.3 mOhm) blocks back-feed from the Pi\'s own USB-C.\n'
        'C24 (VCAP) connects to ANODE per the LM74700 datasheet; rated >= 25 V. 100 nF = 2.3 ms turn-on delay.\n'
        'BRING-UP: 3.3 mOhm is below TI\'s 8-20 mOhm window - scope the gate with the Pi idling for hunting.', (170, 22))
pi.add('console_server:HYG032N03LR1C1', (230, 60), 'Q1', 'AON7524', {'1': '+5V', '4': 'Q1_GATE', '5': 'PI_5V'},
       rot=270, fields=flds('Q:HYG032'))
pi.add('Power_Management:LM74700', (230, 90), 'U19', 'LM74700QDBVTQ1',
       {'1': 'LM_VCAP', '2': 'GND', '3': '+5V', '4': 'PI_5V', '5': 'Q1_GATE', '6': '+5V'}, fields=flds('U:LM74700'))
r = Row(pi, 260, 85)
r.C('100nF', 'LM_VCAP', '+5V', key='C:100nF50', ref='C24', extra={'Note': 'VCAP to ANODE, >=25 V'})
r.C('10uF', '+5V', 'GND', key='C:10uF', fp=FP['C0805'])
pi.add('Diode:SMAJ6.0A', (300, 92), 'D', 'SMAJ6.0A', {'1': 'PI_5V', '2': 'GND'}, rot=90, fields=flds('D:SMAJ6.0A'))
r = Row(pi, 315, 85)
r.CP('470uF', 'PI_5V', 'GND', 'C:470uF10', 'Capacitor_SMD:CP_Elec_6.3x7.7')
r.CP('220uF', 'PI_5V', 'GND', 'C:220uF10', 'Capacitor_SMD:CP_Elec_6.3x5.4')
r.C('10uF', 'PI_5V', 'GND', key='C:10uF', fp=FP['C0805'])
flag(pi, 'PI_5V', 360, 80)

# HAT ID EEPROM
pi.text('HAT ID EEPROM on ID_SD / ID_SC (pins 27/28). Bridge JP to GND to write.', (170, 125))
pi.add('Memory_EEPROM:24LC32', (200, 150), 'U21', '24LC32A-I/SN',
       {'1': 'GND', '2': 'GND', '3': 'GND', '4': 'GND', '5': 'ID_SD', '6': 'ID_SC', '7': 'EE_WP', '8': 'PI3.3'},
       fp='Package_SO:SOIC-8_3.9x4.9mm_P1.27mm', fields=flds('U:24LC32A'))
r = Row(pi, 240, 145)
r.R('3.9k', 'PI3.3', 'ID_SD')
r.R('3.9k', 'PI3.3', 'ID_SC')
r.R('10k', 'PI3.3', 'EE_WP')
r.C('100nF', 'PI3.3', 'GND')
pi.add('Jumper:SolderJumper_2_Open', (290, 150), 'JP', 'WP_EN', {'1': 'EE_WP', '2': 'GND'},
       fp='Jumper:SolderJumper-2_P1.3mm_Open_RoundedPad1.0x1.5mm', in_bom=False)

# Pi console UART -> RS-232 on a 3-pin header
pi.text('Pi console UART (GPIO14/15) to RS-232 on a 3-pin header. VCC from PI3.3: unpowered on boards without a Pi.\n'
        'Header: TX - GND - RX, ground in the centre.', (170, 190))
pi.add('Interface_UART:MAX3232', (230, 240), 'U18', 'SP3232EEN-L/TR',
       {'1': 'DBG_C1P', '3': 'DBG_C1N', '4': 'DBG_C2P', '5': 'DBG_C2N', '2': 'DBG_VP', '6': 'DBG_VN',
        '11': 'PITXD', '12': 'PIRXD', '14': 'DBG_TX', '13': 'DBG_RX', '10': 'GND', '9': None, '7': None, '8': None,
        '15': 'GND', '16': 'PI3.3'},
       fp='Package_SO:SOIC-16_3.9x9.9mm_P1.27mm', fields=flds('U:SP3232'))
r = Row(pi, 290, 230)
r.C('100nF', 'DBG_C1P', 'DBG_C1N')
r.C('100nF', 'DBG_C2P', 'DBG_C2N')
r.C('100nF', 'DBG_VP', 'GND')
r.C('100nF', 'GND', 'DBG_VN')
r.C('100nF', 'PI3.3', 'GND')
pi.add('Connector_Generic:Conn_01x03', (370, 240), 'J6', 'PI_CONSOLE', {'1': 'DBG_TX', '2': 'GND', '3': 'DBG_RX'},
       fp='Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical', fields=flds('J:1x03'), rot=180)

# =====================================================================
# USB upstream + hub
# =====================================================================
hub = P.sheet('hub.kicad_sch', 'USB upstream and hub')
hub.text('USB-C UPSTREAM. PRTR5V0U2F routed THROUGH (pins 1/6 one channel, 3/4 the other) - no stub.\n'
         'Chosen over USBLC6 for its GND-VCC diode: protected with no host / no VBUS present.\n'
         'CC1/CC2 terminate in 5.1k Rd only. A6/B6 and A7/B7 tied for either orientation.', (20, 22))
hub.add('Connector:USB_C_Receptacle_USB2.0_16P', (45, 85), 'J1', 'USB-C',
        {'A1': 'GND', 'A12': 'GND', 'B1': 'GND', 'B12': 'GND', 'A4': 'VBUS_UP', 'A9': 'VBUS_UP', 'B4': 'VBUS_UP',
         'B9': 'VBUS_UP', 'A5': 'CC1', 'B5': 'CC2', 'A6': 'USB_UP_DP', 'B6': 'USB_UP_DP', 'A7': 'USB_UP_DM',
         'B7': 'USB_UP_DM', 'A8': None, 'B8': None, 'SH': 'USB_SHIELD'},
        fp='Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12', fields=flds('J:USBC'), label_len=5.08)
hub.add('console_server:PRTR5V0U2F', (110, 70), 'U22', 'PRTR5V0U2F',
        {'1': 'USB_UP_DP', '6': 'USB_UP_DP', '3': 'USB_UP_DM', '4': 'USB_UP_DM', '5': 'VBUS_UP', '2': 'GND'},
        fields=flds('U:PRTR5V0U2F'))
r = Row(hub, 20, 125)
r.R('5.1k', 'CC1', 'GND')
r.R('5.1k', 'CC2', 'GND')
r.C('1uF', 'VBUS_UP', 'GND', key='C:1uF50', fp=FP['C0805'], ref='C174')
r.R('47k', 'VBUS_UP', 'VBUS_DET')
r.R('100k', 'VBUS_DET', 'GND')
r.R('1M', 'USB_SHIELD', 'GND')
r.C('4.7nF', 'USB_SHIELD', 'GND', key='C:4.7nF')

hub.text('USB2514BI HUB, I2C-EEPROM config mode: CFG_SEL[1:0] = 11 (pull-ups on pins 24, 25).\n'
         'Straps are ignored in this mode - the 24LC02B carries: all 4 ports non-removable, OC sensing unused,\n'
         'self-powered, VID/PID 0424/2514, serial number set. TEST to GND. No port power switches.\n'
         'PORT SWAP: downstream D+/D- are crossed at pins 1-4 and 6-9 for routing - set PRTSP (0xFA) bits 1-4\n'
         '= 0x1E in the EEPROM image, or ports 1-4 will not enumerate.\n'
         'R28 (SCL / CFG_SEL0 pull-up) is also the strap: firmware must release SCL/SDA before HUB_MR_N.\n'
         'NOTE: record lists 0.01 uF + 0.1 uF on CRFILT/PLLFILT; the USB2514B datasheet asks for >= 1.0 uF\n'
         'low-ESR on each - fitted 1 uF + 100 nF here. Confirm against the datasheet.', (150, 22))
# Downstream D+/D- are SWAPPED at the hub pins (PRTSP port swap, set in the 24LC02B): both the hub and
# the FT4232Hs put D- above D+ on the faces that point at each other, so an unswapped coupled pair
# would have to cross over. Pin 1 is physically DM_DN1 but carries D+ once PRTSP bit 1 is set.
hub_nets = {
    '1': 'USB_DN1_DP', '2': 'USB_DN1_DM', '3': 'USB_DN2_DP', '4': 'USB_DN2_DM', '6': 'USB_DN3_DP', '7': 'USB_DN3_DM',
    '8': 'USB_DN4_DP', '9': 'USB_DN4_DM', '5': 'VDDA33', '10': 'VDDA33', '29': 'VDDA33', '36': 'VDDA33',
    '11': 'GND', '12': None, '16': None, '18': None, '20': None, '13': 'OCS_N', '17': 'OCS_N', '19': 'OCS_N',
    '21': 'OCS_N', '14': 'CRFILT', '15': '+3.3V', '23': '+3.3V', '22': 'HUB_SDA', '24': 'HUB_SCL', '25': 'CFG_SEL1',
    '26': 'HUB_MR_N', '27': 'VBUS_DET', '28': 'LOCAL_PWR', '30': 'USB_UP_DM', '31': 'USB_UP_DP', '32': 'HUB_XO',
    '33': 'HUB_XI', '34': 'PLLFILT', '35': 'RBIAS', '37': 'GND'}
hub.add('Interface_USB:USB2514B_Bi', (215, 130), 'U5', 'USB2514BI-AEZG-TR', hub_nets, fields=flds('U:USB2514B'),
        label_len=5.08)
hub.add('Device:Crystal_GND24', (300, 100), 'X1', '24MHz', {'1': 'HUB_XI', '3': 'HUB_XO', '2': 'GND', '4': 'GND'},
        fp='Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm', fields=flds('X:24M', Note='X322524MOB4SI, CL 12 pF'))
r = Row(hub, 290, 125)
r.C('15pF', 'HUB_XI', 'GND', ref='C62', extra={'Note': 'C0G, matched to 12 pF CL'})
r.C('15pF', 'HUB_XO', 'GND', ref='C63', extra={'Note': 'C0G, matched to 12 pF CL'})
r.R('1M', 'HUB_XI', 'HUB_XO', ref='R43', dnp=True, extra={'Note': 'DNP - feedback is internal'})
r = Row(hub, 150, 200)
r.R('10k', '+3.3V', 'HUB_SCL', ref='R28', extra={'Note': 'SCL pull-up AND CFG_SEL0 strap'})
r.R('10k', '+3.3V', 'CFG_SEL1')
r.R('10k', '+3.3V', 'HUB_SDA')
r.R('10k', '+3.3V', 'OCS_N')
r.R('10k', '+3.3V', 'LOCAL_PWR')
r.R('10k', '+3.3V', 'HUB_MR_N')
r.C('100nF', 'HUB_MR_N', 'GND')
r.R('12k', 'RBIAS', 'GND', key='R:12k')
r.C('1uF', 'CRFILT', 'GND', key='C:1uF')
r.C('100nF', 'CRFILT', 'GND')
r.C('1uF', 'PLLFILT', 'GND', key='C:1uF')
r.C('100nF', 'PLLFILT', 'GND')
r = Row(hub, 150, 245)
r.FB('+3.3V', 'VDDA33')
r.C('4.7uF', '+3.3V', 'GND', key='C:4.7uF', fp=FP['C0805'])
r.C('4.7uF', 'VDDA33', 'GND', key='C:4.7uF', fp=FP['C0805'])
for _ in range(4):
    r.C('100nF', 'VDDA33', 'GND')
r.C('100nF', '+3.3V', 'GND')
r.C('100nF', '+3.3V', 'GND')
flag(hub, 'VDDA33', r.x + 5, 235)
hub.text('Hub config EEPROM - private bus. Never on the inter-board harness:\n'
         'three 24LC02Bs at 0xA0 and three hub masters would collide.', (300, 160))
hub.add('Memory_EEPROM:24LC02', (330, 190), 'U20', '24LC02B-I/SN',
        {'1': 'GND', '2': 'GND', '3': 'GND', '4': 'GND', '5': 'HUB_SDA', '6': 'HUB_SCL', '7': 'GND', '8': '+3.3V'},
        fp='Package_SO:SOIC-8_3.9x4.9mm_P1.27mm', fields=flds('U:24LC02B'))
Row(hub, 360, 185).C('100nF', '+3.3V', 'GND')

# =====================================================================
# FT4232H cluster (instantiated 4x): FT4232H + crystal + EEPROM + 2x MAX3232E
# =====================================================================
ft = P.sheet('ftdi.kicad_sch', 'FT4232H cluster (x4): 4 UART channels -> 4 RS-232 ports')
ft.text('ONE OF FOUR IDENTICAL CLUSTERS. Compound device: no VBUS at the FTDI, no VBUS divider into RESET#.\n'
        'VREGOUT -> VCORE is an EXTERNAL connection (4.7 uF compensates the internal LDO).\n'
        'VPLL and VPHY are 3.3 V, each on its own ferrite. All four VCCIO pins must be connected.\n'
        'TEST hard to GND (floating = factory test mode, never enumerates). Channel D RXD is pin 52, not 49.\n'
        'Only TXD/RXD used per channel; modem pins have internal 40-190k pulls.', (20, 22))
ft_idx = lambda i: i  # noqa
ft_nets = {str(n): None for n in range(1, 65)}
ft_nets.update({'2': 'OSCI', '3': 'OSCO', '4': 'VPHY', '6': 'REF', '7': 'USB_DM', '8': 'USB_DP', '9': 'VPLL',
                '12': 'VCORE', '37': 'VCORE', '64': 'VCORE', '49': 'VCORE', '13': 'GND', '14': 'FTDI_RST_N',
                '16': 'TXD_A', '17': 'RXD_A', '26': 'TXD_B', '27': 'RXD_B', '38': 'TXD_C', '39': 'RXD_C',
                '48': 'TXD_D', '52': 'RXD_D', '20': '+3.3V', '31': '+3.3V', '42': '+3.3V', '56': '+3.3V',
                '50': '+3.3V', '61': 'EEDATA', '62': 'EECLK', '63': 'EECS', '10': 'GND'})
for g in (1, 5, 11, 15, 25, 35, 47, 51):
    ft_nets[str(g)] = 'GND'
ft.add('Interface_USB:FT4232H', (120, 140), lambda i: f'U{i + 1}', 'FT4232HL', ft_nets,
       fp='Package_QFP:LQFP-64_10x10mm_P0.5mm', fields=flds('U:FT4232HL'), label_len=5.08)
ft.add('Device:Crystal_GND24', (45, 210), 'X', '12MHz', {'1': 'OSCI', '3': 'OSCO', '2': 'GND', '4': 'GND'},
       fp='Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm', fields=flds('X:12M', Note='X322512MSB4SI, CL 20 pF'))
r = Row(ft, 30, 230)
r.C('33pF', 'OSCI', 'GND', extra={'Note': 'C0G'})
r.C('33pF', 'OSCO', 'GND', extra={'Note': 'C0G'})
r.R('12k', 'REF', 'GND', key='R:12k')
ft.add('console_server:93LC46B', (60, 270), lambda i: f'U{31 + i}', '93LC46BT-I/SN',
       {'1': 'EECS', '2': 'EECLK', '3': 'EEDATA', '4': 'EE_DO', '5': 'GND', '8': '+3.3V'},
       fields=flds('U:93LC46B'), label_len=5.08)
r = Row(ft, 100, 262)
r.R('2.2k', 'EE_DO', 'EEDATA')
r.R('10k', '+3.3V', 'EE_DO')
r.C('100nF', '+3.3V', 'GND')
# FT4232H supply decoupling
r = Row(ft, 150, 225)
r.C('100nF', '+3.3V', 'GND', extra={'Note': 'VREGIN'})
r.C('4.7uF', 'VCORE', 'GND', key='C:4.7uF', fp=FP['C0805'], extra={'Note': 'VREGOUT: LDO compensation, min 3.3 uF'})
for _ in range(3):
    r.C('100nF', 'VCORE', 'GND', extra={'Note': 'VCORE pin'})
for _ in range(4):
    r.C('100nF', '+3.3V', 'GND', extra={'Note': 'VCCIO pin'})
r.C('10uF', '+3.3V', 'GND', key='C:10uF', fp=FP['C0805'], extra={'Note': 'local bulk'})
r = Row(ft, 150, 262)
r.FB('+3.3V', 'VPLL')
r.C('100nF', 'VPLL', 'GND')
r.C('1uF', 'VPLL', 'GND', key='C:1uF')
r.gap()
r.FB('+3.3V', 'VPHY')
r.C('100nF', 'VPHY', 'GND')
r.C('1uF', 'VPHY', 'GND', key='C:1uF')
flag(ft, 'VPLL', 260, 255)
flag(ft, 'VPHY', 270, 255)

ft.text('RS-232: MAX3232E (+-15 kV ESD, 250 kbit/s guaranteed). 3-wire: the Cisco console port ignores RTS/CTS.\n'
        'Flying caps and V+/V- reservoirs tight to the chip - that is the only layout rule.', (210, 22))
for k, (chs, y) in enumerate(((('A', 'B'), 85), (('C', 'D'), 185))):
    a, b = chs
    ft.add('Interface_UART:MAX3232', (300, y), (lambda kk: (lambda i: f'U{10 + 2 * i + kk}'))(k), 'MAX3232EIDR',
           {'1': f'C1P_{a}{b}', '3': f'C1N_{a}{b}', '4': f'C2P_{a}{b}', '5': f'C2N_{a}{b}', '2': f'VP_{a}{b}',
            '6': f'VN_{a}{b}', '11': f'TXD_{a}', '12': f'RXD_{a}', '10': f'TXD_{b}', '9': f'RXD_{b}',
            '14': f'TX232_{a}', '13': f'RX232_{a}', '7': f'TX232_{b}', '8': f'RX232_{b}', '15': 'GND', '16': '+3.3V'},
           fp='Package_SO:SOIC-16_3.9x9.9mm_P1.27mm', fields=flds('U:MAX3232E'), label_len=5.08)
    r = Row(ft, 350, y - 25)
    r.C('100nF', f'C1P_{a}{b}', f'C1N_{a}{b}')
    r.C('100nF', f'C2P_{a}{b}', f'C2N_{a}{b}')
    r.C('100nF', f'VP_{a}{b}', 'GND')
    r.C('100nF', 'GND', f'VN_{a}{b}')
    r.C('100nF', '+3.3V', 'GND')
ft.hier = {'USB_DP': 'bidirectional', 'USB_DM': 'bidirectional', 'FTDI_RST_N': 'input'}
for ch in 'ABCD':
    ft.hier[f'TX232_{ch}'] = 'output'
    ft.hier[f'RX232_{ch}'] = 'input'

# =====================================================================
# Serial ports: one 2x8 ganged RJ45 block
# =====================================================================
pt = P.sheet('ports.kicad_sch', 'RS-232 console ports P01-P16')
pt.text('HC-RJ45-059D-2x8-2-04: one 2x8 ganged block, 16 jacks, NO MAGNETICS (8 signal pins per jack).\n'
        'Pinout is Cisco "Console Port Mode 2" (rolled in the PCB): 3 = RxD in, 6 = TxD out, 4/5 = GND, 1/2/7/8 open.\n'
        'Straight-through Cat5 patch cables work; no blue rollover cable needed.\n'
        'Column j (0..7, left to right): P(2j+1) = lower jack, pads 24j+1..8;  P(2j+2) = upper jack, pads 24j+9..16.\n'
        'The upper jack is inverted and numbered the other way - VERIFY with a 3-6 loopback on one upper and one lower\n'
        'jack at bring-up. A mistake here gives eight silently dead ports that no ERC/DRC catches.', (20, 22))
for port in range(1, 17):
    col, row = (port - 1) % 8, (port - 1) // 8
    pt.add('console_server:HC-RJ45-059D-2x8', (40 + col * 46, 90 + row * 70), 'J5', 'HC-RJ45-059D-2x8-2-04',
           {str(24 * ((port - 1) // 2) + (0 if port % 2 else 8) + c): n for c, n in
            {1: None, 2: None, 3: f'P{port:02d}_RXD', 4: 'GND', 5: 'GND', 6: f'P{port:02d}_TXD', 7: None, 8: None}.items()},
           unit=port, fields=flds('J:RJ45x16'), label_len=5.08)
pt.add('console_server:HC-RJ45-059D-2x8', (60, 240), 'J5', 'HC-RJ45-059D-2x8-2-04', {'193': 'RJ_SHIELD'}, unit=17,
       fields=flds('J:RJ45x16'))
pt.text('SHIELD: all 16 shells common through the frame, one connection point.\n'
        'Footprint set 0R / high-value R / C - 0R fitted. Keeps the option to break the\n'
        'sixteen parallel ground paths that shielded patch cables would otherwise create.', (90, 225))
r = Row(pt, 100, 255)
r.R('0', 'RJ_SHIELD', 'GND', key='R:0')
r.R('1M', 'RJ_SHIELD', 'GND', dnp=True)
r.C('4.7nF', 'RJ_SHIELD', 'GND', key='C:4.7nF', dnp=True)

# =====================================================================
# Housekeeping MCU + inter-board signal harness
# =====================================================================
mc = P.sheet('mcu.kicad_sch', 'Housekeeping MCU and inter-board link')
mc.text('ATtiny3216 at 10 MHz (internal 20 MHz / 2; 20 MHz needs 4.5 V). I2C target to the Pi at 0x30/0x31/0x32.\n'
        'Keep a pre-computed register snapshot so a read never stretches SCL (Broadcom BSC). 100 kHz.\n'
        'Tri-state PC2/PC3 (hub EEPROM bus) whenever +3.3V is down - the MCU outlives it by design.\n'
        'ALERT is open-drain in firmware; pull-up to PI3.3 so it exists only with a Pi (no back-power).\n'
        'Do not fuse PA0 to RESET: recovering UPDI needs a 12 V pulse.', (20, 22))
mc.add('MCU_Microchip_ATtiny:ATtiny3216-S', (90, 130), 'U6', 'ATtiny3216-SN',
       {'1': '+3V3_MCU', '2': 'SENSE_24V', '3': 'SENSE_5V', '4': 'SENSE_3V3', '5': 'MCU_PA7', '6': 'ID1', '7': 'ID0',
        '8': 'SPARE2', '9': 'SPARE1', '10': 'PI_SDA', '11': 'PI_SCL', '12': 'HUB_MR_N', '13': 'FTDI_RST_N',
        '14': 'HUB_SDA', '15': 'HUB_SCL', '16': 'UPDI', '17': 'LED_STATUS', '18': 'ALERT', '19': 'RESET_REQ',
        '20': 'GND'},
       fields=flds('U:ATtiny3216'), label_len=5.08)
r = Row(mc, 30, 185)
r.C('100nF', '+3V3_MCU', 'GND')
r.C('1uF', '+3V3_MCU', 'GND', key='C:1uF')
mc.add('Connector:TestPoint', (60, 175), 'TP', 'PA7', {'1': 'MCU_PA7'}, fp='TestPoint:TestPoint_Pad_D1.5mm',
       in_bom=False)
mc.add('Connector_Generic:Conn_01x03', (40, 75), 'J7', 'UPDI', {'1': 'UPDI', '2': 'GND', '3': '+3V3_MCU'},
       fp='Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical', fields=flds('J:1x03'), rot=180)
mc.text('UPDI: GND in the centre. A reversed plug swaps\n+3V3 and UPDI but never the reference.', (20, 85), 1.27)

mc.text('RAIL MONITORING: ~2.0 V at nominal vs internal 2.5 V ref. 100 nF at each tap.\n'
        'Senses protected +24V, not +24V_RAW. JP2 selects 100 nF (default) or 22 uF on the 24 V tap:\n'
        'SHIP ON 100 nF - 22 uF x 3.9k = 86 ms averages away the dip the monitor exists to catch.', (170, 22))
r = Row(mc, 175, 55)
r.R('47k', '+24V', 'SENSE_24V')
r.R('4.3k', 'SENSE_24V', 'GND')
r.C('100nF', 'TAP24_A', 'GND')
r.C('22uF', 'TAP24_B', 'GND', key='C:22uF', fp=FP['C1206'])
r.gap()
r.R('7.5k', '+5V', 'SENSE_5V')
r.R('5.1k', 'SENSE_5V', 'GND')
r.C('100nF', 'SENSE_5V', 'GND')
r.gap()
r.R('6.8k', '+3.3V', 'SENSE_3V3')
r.R('10k', 'SENSE_3V3', 'GND')
r.C('100nF', 'SENSE_3V3', 'GND')
mc.add('Jumper:SolderJumper_3_Bridged12', (200, 85), 'JP2', '100n/22u', {'1': 'TAP24_A', '2': 'SENSE_24V', '3': 'TAP24_B'},
       fp='Jumper:SolderJumper-3_P1.3mm_Bridged12_RoundedPad1.0x1.5mm', in_bom=False)

mc.text('BOARD IDENTITY - the only build difference. 10k pull-ups always fitted; 0R to GND per board:\n'
        '  board 1: ID1 fitted, ID0 fitted -> 0x30    board 2: ID1 fitted, ID0 omitted -> 0x31\n'
        '  board 3: ID1 omitted, ID0 fitted -> 0x32   (external pulls: identity is measurable with a meter)', (170, 110))
r = Row(mc, 180, 140)
r.R('10k', '+3V3_MCU', 'ID1')
r.R('0', 'ID1', 'GND', key='R:0', extra={'Note': 'ID strap: fit on boards 1, 2'})
r.R('10k', '+3V3_MCU', 'ID0')
r.R('0', 'ID0', 'GND', key='R:0', extra={'Note': 'ID strap: fit on boards 1, 3'})
r.gap()
r.R('10k', 'PI3.3', 'ALERT')
r.R('100k', 'RESET_REQ', 'GND')
r.R('10k', '+3.3V', 'FTDI_RST_N', extra={'Note': 'one pull-up for all four FT4232H'})
r.gap()
r.R('1k', 'LED_STATUS', 'LED_A')
mc.add('Device:LED', (r.x + 5, 150), 'D', 'green', {'2': 'LED_A', '1': 'GND'}, rot=90, fp=FP['LED'],
       fields=flds('D:LED_G', Note='heartbeat = healthy; solid or dark = hung'))

mc.text('INTER-BOARD SIGNAL LINK, 9-way (power is a separate 4-way: different pin counts cannot be swapped).\n'
        'J8 header (right edge) mates J9 socket (left edge) of the next board directly - no cable.\n'
        'The invariant is physical position, not pin number. SPARE1/SPARE2 sit on USART0 (PB2/PB3) for an\n'
        'RS-485 migration if the single-ended link misbehaves. ~100 pF vs Pi 1.8k = ~150 ns rise (1 us limit).', (170, 190))
link = {'1': 'GND', '2': 'PI_SDA', '3': 'PI_SCL', '4': 'GND', '5': 'ALERT', '6': 'RESET_REQ', '7': 'GND',
        '8': 'SPARE1', '9': 'SPARE2'}
mc.add('Connector_Generic:Conn_01x09', (230, 250), 'J8', 'LINK', link,
       fp='Connector_PinHeader_2.54mm:PinHeader_1x09_P2.54mm_Horizontal', fields=flds('J:1x09_hdr'), rot=180)
mc.add('Connector_Generic:Conn_01x09', (300, 250), 'J9', 'LINK', link,
       fp='Connector_PinSocket_2.54mm:PinSocket_1x09_P2.54mm_Horizontal', fields=flds('J:1x09_skt'), rot=180)

# =====================================================================
# Test points: every rail, ground near each cluster of probing, the buses and
# the reset/alert lines. Pads only (no BOM line); kept out from under the Pi.
# =====================================================================
TP_BIG = 'TestPoint:TestPoint_Pad_D1.5mm'
TP_SMALL = 'TestPoint:TestPoint_Pad_D1.0mm'


def test_points(sh, x, y, items, label=None):
    if label:
        sh.text(label, (x - 2, y - 9), 1.27)
    for i, (net, big) in enumerate(items):
        sh.add('Connector:TestPoint', (x + i * 12.7, y), 'TP', net.split('/')[-1], {'1': net},
               fp=TP_BIG if big else TP_SMALL, in_bom=False)


test_points(pw, 25, 270, [('+24V_RAW', True), ('+24V', True), ('+5V', True), ('+3.3V', True), ('+3V3_MCU', True),
                          ('GND', True)], 'TEST POINTS - rails')
test_points(pi, 25, 270, [('PI_5V', True), ('PI3.3', True), ('PI_SDA', False), ('PI_SCL', False), ('PITXD', False),
                          ('PIRXD', False)], 'TEST POINTS - Pi power, I2C, console UART (TTL side)')
test_points(hub, 25, 275, [('VBUS_UP', True), ('HUB_MR_N', False), ('HUB_SDA', False), ('HUB_SCL', False),
                           ('GND', True)], 'TEST POINTS - hub')
test_points(ft, 135, 247, [('VCORE', True)], 'TP: 1.8 V core')
test_points(mc, 25, 268, [('ALERT', False), ('RESET_REQ', False), ('FTDI_RST_N', False)], 'TEST POINTS - link and resets')
test_points(pt, 160, 268, [('GND', True)], 'TP: scope ground near the jacks')

# =====================================================================
# Root sheet
# =====================================================================
root = P.sheet(f'{PROJ}.kicad_sch', 'USB Console Server - top level')
P.root = root
root.text('USB CONSOLE SERVER - 16 RS-232 console ports per board, three identical boards -> 48 ports on one Pi.\n'
          'Board identity is two solder straps on the MCU sheet; nothing else differs between builds.\n'
          'Signal: USB-C -> USB2514B hub -> 4x FT4232H -> 8x MAX3232E -> 16x RJ45 (Cisco console, 3-wire).\n'
          'Power: 24 V cabinet bus -> fuse / reverse-block / TVS -> 5 V (Pi), 3.3 V (logic), LM317 3.3 V (MCU).', (20, 22))
mh = [('H1', 'MountingSlot_3.2x6mm'), ('H2', 'MountingSlot_3.2x6mm'), ('H3', 'MountingSlot_3.2x6mm'),
      ('H4', 'MountingSlot_3.2x6mm'), ('H5', 'Pi'), ('H6', 'Pi'), ('H7', 'Pi'), ('H8', 'Pi')]
for i, (ref, kind) in enumerate(mh):
    root.add('Mechanical:MountingHole', (30 + 12 * i, 270), ref, 'M3 slot' if kind != 'Pi' else 'M2.5 Pi',
             fp='console_server:MountingSlot_3.2x6mm' if kind != 'Pi' else 'MountingHole:MountingHole_2.7mm_M2.5',
             in_bom=False)
root.text('H1-H4: slotted chassis mounts (the boards are located by the chassis, not the connectors).\n'
          'H5-H8: Raspberry Pi HAT pattern, 58 x 49 mm.', (30, 255), 1.27)

sheets = [('Power', pw, 'power'), ('Pi', pi, 'pi'), ('USB hub', hub, 'hub'), ('Housekeeping MCU', mc, 'mcu'),
          ('Ports', pt, 'ports')]
P.sheet_refs = []
page = 2
for i, (name, sh, _) in enumerate(sheets):
    P.sheet_refs.append(SheetRef(name, sh, (40 + 70 * i, 60), (50, 25), [], page))
    page += 1
for k in range(4):
    pins = [('USB_DP', 'bidirectional', 'left', f'USB_DN{k + 1}_DP'),
            ('USB_DM', 'bidirectional', 'left', f'USB_DN{k + 1}_DM'),
            ('FTDI_RST_N', 'input', 'left', 'FTDI_RST_N')]
    for j, ch in enumerate('ABCD'):
        port = 4 * k + j + 1
        pins.append((f'TX232_{ch}', 'output', 'right', f'P{port:02d}_TXD'))
        pins.append((f'RX232_{ch}', 'input', 'right', f'P{port:02d}_RXD'))
    P.sheet_refs.append(SheetRef(f'FT{k + 1}', ft, (60 + 85 * k, 130), (40, 25), pins, page))
    page += 1
root.text('FT1..FT4: one FT4232H cluster per hub port. FT1 serves P01-P04 and sits at the port-01 end of the board.\n'
          'Ports are numbered by physical jack; udev symlinks and silkscreen use the same numbers.', (20, 175), 1.27)

# =====================================================================
# Build
# =====================================================================

def classify(project):
    """Decide label kind per (sheet, net)."""
    where = {}
    for f, sh in project.sheets.items():
        for ps in sh.symbols:
            for net in ps.nets.values():
                if net:
                    where.setdefault(net, set()).add(f)
    for sr in project.sheet_refs:
        for _, _, _, net in sr.pins:
            where.setdefault(net, set()).add(root.file)

    def kind(file, net):
        if net in POWER_NETS:
            return 'power'
        if file == ft.file:
            return 'hier' if net in ft.hier else 'label'
        return 'global' if len(where.get(net, ())) > 1 or file == root.file else 'label'
    return kind, where


def build():
    os.makedirs(os.path.join(OUT, 'lib'), exist_ok=True)
    order = [root.file] + [sr.sheet.file for sr in P.sheet_refs if sr.sheet is not ft] + [ft.file]
    P.instance_paths = {root.file: ['/' + root.uuid]}
    for sr in P.sheet_refs:
        P.instance_paths.setdefault(sr.sheet.file, []).append(f'/{root.uuid}/{sr.uuid}')
    allocate_refs(P, order)
    kind, where = classify(P)
    W = Writer(P, kind, ft.hier)
    # root sheet: sheet symbols and their label stubs
    extra = []
    for sr in P.sheet_refs:
        e, items = sheet_symbol(P, sr)
        extra.append(e)
        for it in items:
            if isinstance(it, tuple):
                _, net, x, y, dx, dy, seed = it
                W.power(root, extra, net, x, y, dx, dy, seed)
            else:
                extra.append(it)
    for f in order:
        sh = P.sheets[f]
        txt = W.sheet_file(sh, extra if sh is root else ())
        with open(os.path.join(OUT, f), 'w') as fh:
            fh.write(txt)
    write_library(lib, os.path.join(OUT, 'lib', 'console_server.kicad_sym'))
    # single-instance nets used on one sheet only would be fine as local labels; report globals
    return where


if __name__ == '__main__':
    build()
    print('schematic written')
