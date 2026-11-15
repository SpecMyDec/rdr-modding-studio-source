import sys
sys.path.insert(0, r"03_Tools"); sys.path.insert(0, r"03_Tools\research")
from rdrstudio.config import VANILLA_ISO
from rdrstudio.game import Game
from rdrstudio.level import Level
from rdrstudio import scene
g=Game(VANILLA_ISO); lv=Level(g,"LVLS_SNG/SHER70.DAT"); rs=lv._load_rsc()
print(lv.rsc_name, len(rs.chunks), rs.tally())
ms=rs.models(); bs=rs.bounds()
for n in lv.nodes:
    c=scene.chunk_of(n)
    if c==691 or c==692: print("NODE",n.kind,n.name,n.source,c,n.xyz(),[x.value for x in n.rot][:9])
print("model692 verts", len(ms.get(692,{}).get('verts',[])) if 692 in ms else None)
nb=[n for n in lv.nodes if n.kind=='bound']
print("bound nodes", len(nb), [ (n.name, scene.chunk_of(n)) for n in nb][:40])
m=ms.get(692) or ms.get(691)
vs=m['verts']; lo=[min(v[i] for v in vs) for i in range(3)]; hi=[max(v[i] for v in vs) for i in range(3)]
print("692 local aabb",lo,hi)
# all instances of zone nodes using 692? 
for n in lv.nodes:
    if scene.chunk_of(n)==692: print('inst',n.name,n.xyz())
