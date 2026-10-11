"""Build lib/console_server.pretty: vendor footprints (EasyEDA/LCSC via easyeda2kicad,
raw copies in tools/vendor) normalised to KiCad 10, plus hand-made mechanical parts."""
import os
import re
import shutil
import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
VENDOR = os.path.join(HERE, 'vendor', 'easyeda.pretty')
VENDOR3D = os.path.join(HERE, 'vendor', 'easyeda.3dshapes')
OUT = os.path.join(PROJ, 'lib', 'console_server.pretty')
OUT3D = os.path.join(PROJ, 'lib', 'console_server.3dshapes')

# vendor file -> (library name, LCSC part)
VENDOR_PARTS = {
    'RJ45-TH_HC-RJ45-059D-2X8-2-04': ('RJ45_HC-RJ45-059D-2x8', 'C46261623'),
    'TO-252-3_L6.5-W6.1-P4.58-LS9.9-BR': ('TO-252-3_L6.5-W6.1-P4.58-LS9.9-BR', 'C2624'),
    'DFN-8_L3.0-W3.0-P0.65-BL-2': ('DFN-8_L3.0-W3.0-P0.65-BL-2', 'C2827233'),
    'SOT886_L1.5-W1.0-P0.50-LS1.00-BL': ('SOT886_L1.5-W1.0-P0.50-LS1.00-BL', 'C478118'),
    'IND-SMD_L13.5-W12.5': ('L_Bourns_SRP1265A', 'C840531'),
}


def mm(v):
    return pcbnew.FromMM(v)


def load_patched(src):
    """EasyEDA quirks: mounting pegs come as plated pads with no annular ring, and some
    parts carry stray marker arcs on copper. Fix both before KiCad loads the file."""
    import tempfile
    text = open(os.path.join(VENDOR, src + '.kicad_mod')).read()
    text = re.sub(r'\(pad "" thru_hole (circle|oval) (\(at [^)]*\)) \(size ([\d.]+) ([\d.]+)\) \(drill ([\d.]+)\) \(layers [^)]*\)\)',
                  lambda m: (f'(pad "" np_thru_hole {m.group(1)} {m.group(2)} (size {m.group(3)} {m.group(4)}) '
                             f'(drill {m.group(5)}) (layers *.Cu *.Mask))') if m.group(3) == m.group(5) else m.group(0),
                  text)
    text = re.sub(r'\t\(fp_arc [^\n]*\(layer F\.Cu\)[^\n]*\n', '', text)
    with tempfile.TemporaryDirectory() as td:
        lib = os.path.join(td, 'v.pretty')
        os.makedirs(lib)
        open(os.path.join(lib, src + '.kicad_mod'), 'w').write(text)
        return pcbnew.FootprintLoad(lib, src)


def vendor():
    for src, (name, lcsc) in VENDOR_PARTS.items():
        if not os.path.exists(os.path.join(VENDOR, src + '.kicad_mod')):
            continue
        fp = load_patched(src)
        fp.SetFPID(pcbnew.LIB_ID('console_server', name))
        fp.SetLibDescription(f'{name} (LCSC {lcsc}, EasyEDA footprint via easyeda2kicad)')
        pcbnew.FootprintSave(OUT, fp)
        # point the 3D model at the project copy of the STEP file
        path = os.path.join(OUT, name + '.kicad_mod')
        text = open(path).read()

        def repl(m):
            step = os.path.splitext(os.path.basename(m.group(1)))[0] + '.step'
            if not os.path.exists(os.path.join(VENDOR3D, step)):
                return m.group(0)
            shutil.copy(os.path.join(VENDOR3D, step), OUT3D)
            return '(model "${KIPRJMOD}/lib/console_server.3dshapes/' + step + '"'
        open(path, 'w').write(re.sub(r'\(model "([^"]+)"', repl, text))


def mounting_slot():
    """M3 slotted chassis hole: 3.2 x 6.0 mm NPTH slot."""
    board = pcbnew.BOARD()
    fp = pcbnew.FOOTPRINT(board)
    fp.SetFPID(pcbnew.LIB_ID('console_server', 'MountingSlot_3.2x6mm'))
    fp.SetLibDescription('M3 slotted mounting hole, 3.2 x 6.0 mm slot; absorbs tolerance across the board chain')
    fp.SetAttributes(pcbnew.FP_EXCLUDE_FROM_POS_FILES | pcbnew.FP_EXCLUDE_FROM_BOM)
    pad = pcbnew.PAD(fp)
    pad.SetAttribute(pcbnew.PAD_ATTRIB_NPTH)
    pad.SetShape(pcbnew.PAD_SHAPE_OVAL)
    pad.SetDrillShape(pcbnew.PAD_DRILL_SHAPE_OBLONG)
    pad.SetSize(pcbnew.VECTOR2I(mm(6.0), mm(3.2)))
    pad.SetDrillSize(pcbnew.VECTOR2I(mm(6.0), mm(3.2)))
    pad.SetLayerSet(pad.UnplatedHoleMask())
    pad.SetNumber('')
    fp.Add(pad)
    for layer, w in ((pcbnew.F_CrtYd, 0.05), (pcbnew.Cmts_User, 0.15)):
        r = pcbnew.PCB_SHAPE(fp)
        r.SetShape(pcbnew.SHAPE_T_RECT)
        r.SetStart(pcbnew.VECTOR2I(mm(-5.0), mm(-3.3)))
        r.SetEnd(pcbnew.VECTOR2I(mm(5.0), mm(3.3)))
        r.SetLayer(layer)
        r.SetWidth(mm(w))
        fp.Add(r)
    fp.Reference().SetText('REF**')
    fp.Reference().SetPosition(pcbnew.VECTOR2I(0, mm(-4.5)))
    fp.Reference().SetLayer(pcbnew.F_SilkS)
    fp.Value().SetText('MountingSlot_3.2x6mm')
    fp.Value().SetPosition(pcbnew.VECTOR2I(0, mm(4.5)))
    fp.Value().SetLayer(pcbnew.F_Fab)
    pcbnew.FootprintSave(OUT, fp)


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(OUT3D, exist_ok=True)
    vendor()
    mounting_slot()
    print(sorted(os.listdir(OUT)))
