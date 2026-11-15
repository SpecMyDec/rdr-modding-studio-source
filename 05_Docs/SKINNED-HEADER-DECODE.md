# Skinned character packets: the header words, decoded

2026-11-05, daylane. Evidence: `03_Tools/scripts/test_skinvif.py` (reads
01_Vanilla, writes nothing) and the research probe
`03_Tools/research/probe_skinned_header.py`. Population: every rigged
(bone-count > 0) `MOD0` in every unique `*_lods.rsc` on the disc: 365 models,
420 unique parts, **15,813 batches** (15,361 with 16-bit positions, 452 with
float positions). The earlier "48,451 batches" figure counted duplicated
bundles.

## One batch (16-bit positions)

```
STMASK 40404040 | STMOD 0 | STCYCL 0x103          (write every 3rd VU quadword)
UNPACK V4_32 x1  @T       header qword
UNPACK V4_32 x1  @T+1     GIF tag
STMASK 50505050 | STROW (2048,2048,..)  | STMOD 1 | UNPACK V2_16 xN @T+2  UVs
STMASK 40404040 x2 | STROW (65536,..)    | STMOD 1 | UNPACK V3_8  xN @T+3  normals
STROW pos-row | STMOD 1 | UNPACK V4_16 xN @T+4                           positions
UNPACK V3_32 x1  @0x28    bias
[STCYCL 0x101 | UNPACK V4_32 x(2*nrec) @T+176-2*nrec]                    blend influences
ITOP T | MSCAL 0 | NOP x k
```

## The header qword (the open question)

`(w0, w1, w2, w3) = (6, nrec, 0x1116 | (N+26)<<16, B | C<<16)`

| field | meaning | evidence (15,813 batches unless noted) |
|---|---|---|
| w0 | constant 6 | 15,813 / 15,813 |
| **w1** | **number of secondary-influence records** (the V4_32 block holds 2 quadwords each) | `w1 == extra_quadwords/2` 15,813 / 15,813; w1 = 0 exactly when there is no block (4,335) |
| w2 low | constant 0x1116 (rigid level batches use 0x142) | 15,813 / 15,813 |
| w2 high **A** | N + 26 (N = vertices in the batch) | 15,813 / 15,813 |
| **B** | the batch's own size in 32-bit words, NOP pad included (STMASK to the next batch) | 15,393 exact; the other 420 are the LAST batch of a part, where the measured span also covers the closing STCYCL+FLUSH (+2 words) |
| **C** | **36 + ceil(7N/4)** | 15,813 / 15,813 (fitted as `1.751N + 36.3`, then checked exact) |

The previous note said "the second word varies 0..30, not the bone count"
and "C = 2N+22 holds on 722": the second word is the influence-record count,
and C was fitted to the wrong constant (2N+22 is the *rigid* level shape).

Other fixed words, all measured: GIF tag `(0x8000|N, 0x30024004, 0x512, N)`;
STMASK 0x40404040 / 0x50505050; UV row `(2048, 2048, 0x45009000, 0x45008000)`;
normal row `(65536, 65536, 65536, 0x47800080)`; position row either
`0x44800000 x3` (range ±4 m, step 1/8192 m; 13,808 batches) or `0x46000000 x3`
(±32 m, step 1/1024 m; 1,553). The V3_32 bias at 0x28 is the position row
(+0x8000) shifted per axis; the shift is free (e.g. +0x200 on Y).

* **VU buffer base T** = `0xD2 + 4*bones + k*S`, k = 0,1,2 repeating from the
  start of each part, `S = (1024 - (0xD2+4*bones)) // 3`. 15,813 / 15,813.
* **Influence block** starts at `T + 176 - 2*nrec` (all 11,391 batches that have
  one). Records are `(ox,oy,oz,weight)` in the extra bone's local frame and
  `(T+4+3*vertex, 0xD2+4*bone, 0xCDCDCDCD, 0xCDCDCDCD)`. The 0xCD words are
  uninitialised-memory fill; 28,433 records carry zeros instead (free).
* **NOP pad**: the default makes the batch a whole number of quadwords
  (14,961 + 144 of 15,361). The other 256 are all the FIRST batch of a part,
  2 words longer. A free choice, so the encoder carries `pad`.
* Normals are V3_8, 3 bytes per vertex padded to a word: pad bytes are 0xCD
  (9,394) or 0 (1,194): free.
* Per-vertex W = `ADC<<15 | slot`, slot = `0xD2 + 4*bone`. The first two
  vertices of a batch always have ADC set; no vertex uses slot 0.
* Batch budget: `3N + 4 + 2*nrec <= 176` holds for all but 27 batches (max 178).

## What a byte-identical round trip needed

`skinvif.encode_batch` rebuilds **15,361 / 15,361** narrow batches from fields
(header words, GIF tag, addresses, strides all derived by rule); the rigged
container (`parse_container`/`build_container`) rebuilds **365 / 365**; whole part
packets (prologue + encoded batches + epilogue) rebuild **420 / 420**. The 452
float-position batches are copied through, not encoded.

## Still NOT decoded (estimated by the writer, labelled as such)

* The part record's **index count** (`indices`). The vertex count equals the sum
  of N (1,091 / 1,091 parts) but the index count follows no exact formula:
  roughly `N + 12..16 + 2*nrec` per batch. The writer over-estimates (`N+15+2*nrec`).
* The **cull sphere** radius in the part prologue (centre is 0,0,0 in 288 / 400
  parts; radius ≈ 0.98 x the largest bone-local vertex distance, exact only on
  31 / 400). The writer uses 1.1 x that + 0.01.
* The microcode's use of w0 = 6 and the 0x1116 constant. They never vary, so
  they are copied.
* Float-position (452) batches: round-trip only, no encoder.
* ~~Whether the game draws a custom character at all.~~ **ANSWERED 2026-11-12: yes.** A re-striped custom mesh
  (town player, head x1.7) draws, textures and animates in PCSX2; see DEEPDIVE-2026-11-11 section 5.
