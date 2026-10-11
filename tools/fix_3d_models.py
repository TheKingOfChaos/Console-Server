"""Point the board's 3D models at files that exist.
KiCad 10's stock library has no model for some stock footprints (or renamed it); use the
EasyEDA model (copied into lib/console_server.3dshapes) or the renamed stock file instead.

    python3 tools/fix_3d_models.py
"""
import os
import pcbnew

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PCB = os.path.join(ROOT, 'usb-console-server.kicad_pcb')
LIB = '${KIPRJMOD}/lib/console_server.3dshapes/'
STOCK = '${KICAD10_3DMODEL_DIR}/'
# ref: (file, offset xyz mm (KiCad model frame, +y up), rotation xyz deg)
MODELS = {
    'L2': (LIB + 'IND-SMD_L7.1-W6.6-H2.8.step', (0, 0, 0), (0, 0, 0)),             # SRP7028A, LCSC C2687402
    'J1': (LIB + 'USB-C_SMD-TYPE-C-31-M-12.step', (0, 1.39, 0), (0, 0, 180)),      # HRO TYPE-C-31-M-12, C165948;
                                                                                    # aligned on the two locating pegs
    'U8': (STOCK + 'Package_SO.3dshapes/HSOP-8-1EP_3.9x4.9mm_P1.27mm_EP2.41x3.1mm.step', (0, 0, 0), (0, 0, 0)),
    'U9': (STOCK + 'Package_SO.3dshapes/TI_SO-PowerPAD-8.step', (0, 0, 0), (0, 0, 0)),
}


def main():
    b = pcbnew.LoadBoard(PCB)
    for ref, (path, off, rot) in MODELS.items():
        fp = b.FindFootprintByReference(ref)
        fp.Models().clear()
        m = pcbnew.FP_3DMODEL()
        m.m_Filename = path
        m.m_Offset = pcbnew.VECTOR3D(*off)
        m.m_Rotation = pcbnew.VECTOR3D(*rot)
        fp.Models().push_back(m)
        print(ref, path)
    pcbnew.SaveBoard(PCB, b)


if __name__ == '__main__':
    main()
