"""Whole-disc probe for rdrstudio/bndtree.py: every BND0 parses as an octree;
a rebuild with nothing moved is compared with the shipped index."""
import sys, struct, collections, time
sys.path.insert(0, r"03_Tools")
from rdrstudio.config import VANILLA_ISO
from rdrstudio.game import Game
from rdrstudio.level import list_levels
from rdrstudio.rsc import Rsc
from rdrstudio import bndtree as T
g = Game(VANILLA_ISO); S = collections.Counter(); seen = set(); ex = []
arcs = set(sys.argv[1:]); t0 = time.time()
def leaves(n, acc):
    if n["kids"]:
        for k in n["kids"]: leaves(k, acc)
    else: acc.append(n)
    return acc
def shape(n):
    return (tuple(shape(k) for k in n["kids"]) if n["kids"] else len(n["faces"] or []))
for L in list_levels(g):
    name = L["archive"] if isinstance(L, dict) else L
    if arcs and name not in arcs: continue
    d = g.archive(name)
    for rn in [e.name for e in d if e.name.lower().endswith(".rsc")]:
        try: r = Rsc(d.read(rn), rn)
        except Exception: continue
        for c in r.chunks:
            if c.tag != "BND0": continue
            raw = r.raw(c.index)
            if raw in seen: continue
            seen.add(raw); S["chunks"] += 1
            try: p = T.parse(raw)
            except Exception as e:
                S["parse_fail"] += 1; ex.append((name, rn, c.index, str(e))); continue
            S["nodes"] += p["node_count"]; S["faces"] += p["nf"]
            lo = struct.unpack_from("<3f", raw, 8); hi = struct.unpack_from("<3f", raw, 20)
            m = struct.unpack_from("<2f", raw, 0x40); me = min(hi[i]-lo[i] for i in range(3))
            S["margin_ok" if abs(m[0]-0.035*me) <= 1e-4+1e-3*m[0] and abs(m[1]-0.3*m[0]) <= 1e-5+1e-3*m[1] else "margin_other"] += 1
            S["len16_ok" if len(raw) % 16 == 0 else "len16_bad"] += 1
            tl = len(raw) - p["toff"]; need = 4*((p["nf"]+31)//32)
            S["tail_ok" if need <= tl < need+16 else "tail_other"] += 1
            a = T.audit(raw)
            S["stock_missing"] += a["missing"]; S["stock_extra"] += a["extra"]; S["stock_tight_bad"] += a["tight_bad"]; S["leaves"] += a["leaves"]
            if a["faces_listed"] != a["faces"]: S["stock_chunks_with_unlisted_faces"] += 1
            nb = T.rebuild(raw)
            if nb == raw: S["rebuild_byte_identical"] += 1
            else:
                q = T.parse(nb)
                if nb[:p["noff"]] != raw[:p["noff"]]: S["prefix_changed_BAD"] += 1
                if shape(q["root"]) == shape(p["root"]): S["same_tree_shape"] += 1
                else:
                    S["different_tree"] += 1
                    if len(ex) < 6: ex.append((name, rn, c.index, len(raw), len(nb), p["node_count"], q["node_count"]))
                S["size_stock"] += len(raw); S["size_rebuilt"] += len(nb)
            b = T.audit(nb); S["rebuilt_missing"] += b["missing"]; S["rebuilt_tight_bad"] += b["tight_bad"]
print("secs", round(time.time()-t0))
for k, v in sorted(S.items()): print(" ", k, v)
print(ex)
