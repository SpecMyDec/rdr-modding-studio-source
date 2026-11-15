"""Probe: how does a level model (zone node -> MOD0) link to its collision (bound node -> BND0)?
Read-only. Corpus-wide over LVLS_SNG/M2/M4 archives (default area each)."""
import sys, math, collections, time
sys.path.insert(0, r"03_Tools")
from rdrstudio.config import VANILLA_ISO
from rdrstudio.game import Game
from rdrstudio.level import Level, list_levels
from rdrstudio import scene

def world_aabb(verts, n):
    # n.pos translation; rot 3x3 if present
    p = n.xyz(); rot = [x.value for x in n.rot] if n.rot else None
    out = []
    for v in verts:
        if rot:
            # row-vector convention: v*M + t
            x = v[0]*rot[0]+v[1]*rot[3]+v[2]*rot[6]+p[0]
            y = v[0]*rot[1]+v[1]*rot[4]+v[2]*rot[7]+p[1]
            z = v[0]*rot[2]+v[1]*rot[5]+v[2]*rot[8]+p[2]
        else:
            x,y,z = v[0]+p[0], v[1]+p[1], v[2]+p[2]
        out.append((x,y,z))
    lo=[min(o[i] for o in out) for i in range(3)]; hi=[max(o[i] for o in out) for i in range(3)]
    return lo,hi

def iou(a,b):
    inter=1.0; 
    for i in range(3):
        l=max(a[0][i],b[0][i]); h=min(a[1][i],b[1][i])
        if h<=l: return 0.0
        inter*= (h-l)
    va=1.0; vb=1.0
    for i in range(3):
        va*=max(a[1][i]-a[0][i],1e-6); vb*=max(b[1][i]-b[0][i],1e-6)
    return inter/(va+vb-inter)

g = Game(VANILLA_ISO)
stats = collections.Counter(); rows=[]
only = sys.argv[1:] 
for a in list_levels(g):
    if only and a not in only: continue
    try:
        lv = Level(g, a); rs = lv._load_rsc()
    except Exception as e:
        stats['level_fail']+=1; continue
    if rs is None: stats['no_rsc']+=1; continue
    link = lv.links(); models = rs.models(); bnds = rs.bounds()
    stats['levels']+=1
    for zu, bu in link.items():
        z = lv.by_uid(zu)
        if z.kind!='zone': continue
        b = lv.by_uid(bu)
        zc, bc = scene.chunk_of(z), scene.chunk_of(b)
        stats['pairs']+=1
        if zc is None or bc is None or zc not in models or bc not in bnds:
            stats['pair_unresolved']+=1; continue
        mv = models[zc]['verts']; bm = bnds[bc]
        if not mv: stats['pair_nomodelverts']+=1; continue
        za = world_aabb(mv, z); ba = world_aabb(bm.verts, b)
        io = iou(za, ba)
        rows.append((a, z.name, b.name, zc, bc, io, za, ba))
        stats['pair_measured']+=1
        stats['adjacent_chunk'] += (abs(zc-bc)==1)
        if io>0: stats['overlap']+=1
        if io>0.3: stats['iou>0.3']+=1
        # centre containment
        zcn=[(za[0][i]+za[1][i])/2 for i in range(3)]
        if all(ba[0][i]-0.5<=zcn[i]<=ba[1][i]+0.5 for i in range(3)): stats['model_centre_in_bound']+=1
print(dict(stats))
import statistics
if rows:
    print("median IoU", statistics.median(r[5] for r in rows))
    for r in rows[:12]: print(r[0], r[1], r[2], 'mod',r[3],'bnd',r[4], 'iou %.2f'%r[5], [round(x,1) for x in r[6][0]+r[6][1]], [round(x,1) for x in r[7][0]+r[7][1]])
