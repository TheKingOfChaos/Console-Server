"""LCSC part numbers and specs. Values from the design record where it gives them,
otherwise confirmed against the JLCPCB catalogue (adom-jlcpcb, 2026-10-10).
'basic' parts carry no extended-part fee at JLCPCB assembly."""

LCSC = {
    # --- from the design record
    'F:0468002': 'C187611', 'Q:IRFR5305': 'C2624', 'D:BZT52C15': 'C668893', 'D:SMAJ26A': 'C148225',
    'U:LM317': 'C5296732', 'X:24M': 'C70590', 'U:FT4232HL': 'C56742', 'X:12M': 'C9002', 'U:93LC46B': 'C16253',
    'U:MAX3232E': 'C193666', 'U:SP3232': 'C9378', 'J:RJ45x16': 'C46261623', 'U:LM74700': 'C2941042',
    'Q:HYG032': 'C2827233', 'D:SMAJ6.0A': 'C223993',
    # --- ICs / discretes confirmed by search
    'U:USB2514B': 'C58940', 'U:24LC02B': 'C1348870', 'U:24LC32A': 'C511262', 'U:PRTR5V0U2F': 'C478118',
    'U:ATtiny3216': 'C609661', 'FB:BLM18KG601SN1D': 'C85833', 'U:TPS54560B': 'C1850354', 'U:TPS54360B': 'C524806',
    'D:B560C': 'C85100', 'D:B360A': 'C94193', 'J:USBC': 'C165948',
    'L:SRP1265A': 'C840531', 'L:SRP7028A': 'C2687402',
    'C:100uF63': 'C51953411', 'C:470uF10': 'C335982', 'C:220uF10': 'C72494',
    'J:2x20_skt': 'C2977589', 'J:1x09_hdr': 'C492417', 'J:1x09_skt': 'C2897391',
    'J:1x04_hdr': 'C492412', 'J:1x04_skt': 'C2897386',
    'D:LED_G': 'C72043',
    # --- passives (basic unless noted)
    'C:100nF': 'C14663', 'C:100nF50': 'C14663', 'C:1uF': 'C29936', 'C:1uF50': 'C28323', 'C:10uF': 'C15850',
    'C:4.7uF': 'C1779', 'C:22uF': 'C12891', 'C:2.2uF100': 'C153036', 'C:33pF': 'C1663', 'C:15pF': 'C1644',
    'C:47pF': 'C1671', 'C:4.7nF': 'C53987', 'C:10nF': 'C57112',
    'R:10k': 'C25804', 'R:1k': 'C21190', 'R:2.2k': 'C4190', 'R:12k': 'C22790', 'R:5.1k': 'C23186',
    'R:4.7k': 'C23162', 'R:47k': 'C25819', 'R:100k': 'C25803', 'R:390': 'C23151', 'R:1M': 'C22935', 'R:0': 'C21189',
    'R:3.9k': 'C23018', 'R:7.5k': 'C23234', 'R:6.8k': 'C23212',
    'R:240': 'C23350', 'R:4.3k': 'C23159', 'R:53.6k': 'C2930115', 'R:31.6k': 'C25967', 'R:243k': 'C23351',
    'R:16.9k': 'C2933158',   # extended
}

SPEC = {
    'C:100nF': '50V X7R', 'C:100nF50': '50V X7R', 'C:1uF': '25V X7R', 'C:1uF50': '50V X7R', 'C:10uF': '25V X5R',
    'C:4.7uF': '25V X5R', 'C:22uF': '25V X5R', 'C:2.2uF100': '100V X7R',
    'C:33pF': '50V C0G', 'C:15pF': '50V C0G', 'C:47pF': '50V C0G', 'C:4.7nF': '50V X7R',
    'C:100uF63': '63V electrolytic 10x10.5', 'C:470uF10': '10V electrolytic 6.3x7.7',
    'C:220uF10': '10V electrolytic 6.3x5.4', 'R:12k': '1%', 'R:0': 'jumper',
}


def spec(key):
    if key in SPEC:
        return SPEC[key]
    if key.startswith('R:'):
        return '1% 0603'
    return None
