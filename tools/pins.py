import sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from sexp import parse, find, first
lib, name = sys.argv[1], sys.argv[2]
root = parse(open(f'/usr/share/kicad/symbols/{lib}.kicad_sym').read())
syms = {s[1]: s for s in find(root, 'symbol')}
s = syms[name]
ext = first(s, 'extends')
if ext: print('extends', ext[1]); s2 = syms[ext[1]]
else: s2 = s
for p in find(s, 'property'):
    if p[1] in ('Footprint', 'Datasheet'): print(p[1], p[2])
pins = []
for u in find(s2, 'symbol'):
    for p in find(u, 'pin'):
        at = first(p, 'at'); nm = first(p, 'name')[1]; nu = first(p, 'number')[1]
        pins.append((u[1], nu, nm, p[1], p[2], at[1:]))
def k(x):
    try: return (0, int(x[1]))
    except: return (1, x[1])
for x in sorted(pins, key=k): print(*x)
