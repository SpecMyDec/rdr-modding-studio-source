// Watch a decoded RDR animation play on its skeleton.
//
// Everything here is CLIENT-SIDE and self-contained: it fetches the raw .anim
// bytes and the raw .skel text through the existing file routes and does the
// whole job in the browser -- decode, forward kinematics, draw. Nothing on the
// server changed to make this work, which is deliberate: the cm2 format is
// fully specified (see rdrstudio/anim.py) and small enough to decode in JS, so
// a playback view need not wait on any new API.
//
// Both halves of the mapping are now read from the engine, not inferred:
// channels 0-2 are the root translation in metres, 3+3i.. are bone i's rotation
// in radians in depth-first order (confirmed by the animation applier at
// 0x002D55B8), and the composition order is YZX -- R = Ry(ch1)*Rz(ch2)*Rx(ch0),
// from the bone matrix builder at 0x00146B68. See rdrstudio/bvh.py for the full
// derivation and for the trap that yields a wrong "XYZ".
//
// three.js's Euler order string 'YZX' produces exactly Ry*Rz*Rx -- verified
// numerically against the engine's form, error 0, where all five other orders
// differ by 0.20 to 0.64. So the order below maps straight through.
//
// All six stay selectable: the derivation is one trace of one function pair,
// and re-exporting is cheap if something still looks wrong.

import * as THREE from 'three';
import { OrbitControls } from './OrbitControls.js';

// ----------------------------------------------------------- cm2 decode (JS)
//
// A direct port of the verified Python decoder. Only the pieces a viewer needs:
// the header, the animated-channel bitmask, the constants, the key matrix and
// the two scales. No interpolation -- stored keys are enough to play.

const DEFAULT_SCALE_A = 1000.0;
const DEFAULT_SCALE_B = 32768.0 / Math.PI;

// ----------------------------------------------------------- cmp decode (JS)
//
// A direct port of rdrstudio/animcmp.py, which was itself read out of the
// unpacked executable (readData at 0x002D35F0, sampler at 0x002D3F08) rather
// than guessed from the bytes. Keep the two in step: if one changes the other
// is wrong, and the browser is the half nobody runs a test against.
//
// There is no separate compressed stream. Each 32-bit word packs three
// channels:
//
//     bits  0..10   11 bits   channel i where i % 3 == 0
//     bits 11..20   10 bits   channel i where i % 3 == 1
//     bits 21..31   11 bits   channel i where i % 3 == 2
//
// The middle field is one bit narrower, which is why it is scaled by 1/1024
// while the other two get 1/2048 -- all three then cover the same range.
//
// Layout: the 17-byte shared header, then `channels` track records of
// (f32 scale, f32 bias, u16 index, u16 discarded), then u16 nchan, then
// nchan*keys packed words, KEY-MAJOR. index 0xFFFF means the channel never
// moves and its value is the bias alone.

// 0x3A001002 and 0x3A802008, loaded as immediates at 0x002D376C / 0x002D3788.
const CMP_SCALE_WIDE = new DataView(
  new Uint32Array([0x3A001002]).buffer).getFloat32(0, true);
const CMP_SCALE_NARROW = new DataView(
  new Uint32Array([0x3A802008]).buffer).getFloat32(0, true);
const CMP_CONSTANT = 0xFFFF;

function decodeCmp(buf, d, frames, channels, keys, loop, halfRate) {
  const HDR = 17;
  if (channels <= 0 || keys <= 0)
    throw new Error(`${channels} channels and ${keys} keys is not a clip`);
  const need = HDR + channels * 12 + 2;
  if (buf.byteLength < need)
    throw new Error(`cmp needs ${need} bytes for ${channels} track records, `
      + `file is ${buf.byteLength}`);

  const scale = new Float32Array(channels);
  const bias = new Float32Array(channels);
  const index = new Uint16Array(channels);
  for (let i = 0; i < channels; i++) {
    const o = HDR + i * 12;
    // Math.fround so the scale matches the Python _f32 exactly rather than
    // drifting a float64 multiply away from it.
    scale[i] = Math.fround(d.getFloat32(o, true)
      * (i % 3 === 1 ? CMP_SCALE_NARROW : CMP_SCALE_WIDE));
    bias[i] = d.getFloat32(o + 4, true);
    index[i] = d.getUint16(o + 8, true);
  }

  const off = HDR + channels * 12;
  const nchan = d.getUint16(off, true);
  const body = off + 2;
  const want = nchan * keys;
  if (body + want * 4 !== buf.byteLength)
    throw new Error(`cmp payload is ${buf.byteLength - body} bytes but `
      + `${nchan} columns x ${keys} keys needs ${want * 4}`);
  const words = new Uint32Array(want);
  for (let i = 0; i < want; i++) words[i] = d.getUint32(body + i * 4, true);

  for (let c = 0; c < channels; c++)
    if (index[c] !== CMP_CONSTANT && index[c] >= nchan)
      throw new Error(`cmp channel ${c} names column ${index[c]} of ${nchan}`);

  const atKey = (k) => {
    const out = new Float32Array(channels);
    const base = k * nchan;
    for (let c = 0; c < channels; c++) {
      const ci = index[c];
      if (ci === CMP_CONSTANT) { out[c] = bias[c]; continue; }
      const w = words[base + ci];
      const slot = c % 3;
      // >>> not >>: bits 21..31 of a word with the top bit set would come
      // back negative under a signed shift and throw the bone a long way out.
      const f = slot === 0 ? (w & 0x7FF)
              : slot === 1 ? ((w >>> 11) & 0x3FF)
              : ((w >>> 21) & 0x7FF);
      out[c] = Math.fround(scale[c] * f + bias[c]);
    }
    return out;
  };

  return { magic: 'cmp', frames, channels, keys, loop, halfRate,
           bones: (channels - 3) / 3, atKey };
}

export function decodeAnim(buf) {
  const d = new DataView(buf);
  const magic = String.fromCharCode(d.getUint8(0), d.getUint8(1), d.getUint8(2));
  const frames = d.getUint32(4, true) & 0xffff;
  const channels = d.getUint32(8, true) & 0xffff;
  const flags = d.getUint8(0x10);
  const loop = !!(flags & 1);
  const halfRate = !!(flags & 8);
  const keys = halfRate ? (frames >> 1) + 1 : frames;

  // THREE codecs reach the viewer. cm2 is the quantised one, cmp packs three
  // channels into each 32-bit word, and the "uncompressed" codec is plain
  // float32 per channel per key. cmp used to be refused here, which left 234
  // clips with a curve list and no way to watch them move.
  if (magic === 'cmp')
    return decodeCmp(buf, d, frames, channels, keys, loop, halfRate);
  if (magic !== 'cm2') {
    // Uncompressed: 17-byte header then f32[channels*keys], row-major by key.
    const want = 17 + 4 * channels * keys;
    if (buf.byteLength !== want)
      throw new Error('not a recognised .anim (size mismatch)');
    const at = (k) => {
      const out = new Float32Array(channels);
      for (let c = 0; c < channels; c++)
        out[c] = d.getFloat32(17 + 4 * (k * channels + c), true);
      return out;
    };
    return { magic, frames, channels, keys, loop, halfRate,
             bones: (channels - 3) / 3, atKey: at };
  }

  let off = 0x11;
  const N = d.getUint32(off, true); off += 4;         // animated-channel count
  const maskBytes = (channels + 7) >> 3;
  const mask = new Uint8Array(buf, off, maskBytes); off += maskBytes;
  const animated = (c) => (mask[c >> 3] >> (c & 7)) & 1;

  const constCount = channels - N;
  const constants = new Int16Array(constCount);
  for (let i = 0; i < constCount; i++) { constants[i] = d.getInt16(off, true); off += 2; }

  const matrix = new Int16Array(N * keys);
  for (let i = 0; i < N * keys; i++) { matrix[i] = d.getInt16(off, true); off += 2; }

  let scaleA = DEFAULT_SCALE_A, scaleB = DEFAULT_SCALE_B;
  if (off + 4 <= buf.byteLength) { scaleA = d.getFloat32(off, true); off += 4; }
  if (off + 4 <= buf.byteLength) { scaleB = d.getFloat32(off, true); off += 4; }
  const dqA = scaleA === 0 ? 0 : 1 / scaleA;
  const dqB = scaleB === 0 ? 0 : 1 / scaleB;

  const atKey = (k) => {
    const out = new Float32Array(channels);
    let ar = 0, cr = 0;
    for (let c = 0; c < channels; c++) {
      const inv = c < 3 ? dqA : dqB;
      const raw = animated(c) ? matrix[k * N + ar++] : constants[cr++];
      out[c] = raw * inv;
    }
    return out;
  };

  return { magic, frames, channels, keys, loop, halfRate,
           bones: (channels - 3) / 3, atKey };
}

// ----------------------------------------------------- .skel parse (JS)
//
// Plain text: `bone NAME { offset x y z  transX ... rotX ...  <nested bones> }`.
// The nesting IS the hierarchy, and file order is depth-first -- which is the
// same order the animation channels follow, so bone index i here maps to
// channels 3+3i.
export function parseSkeleton(text) {
  const bones = [];
  const stack = [];
  const tokens = text.split(/\s+/);
  let i = 0;
  while (i < tokens.length) {
    const t = tokens[i];
    if (t === 'bone') {
      const name = tokens[i + 1];
      // tokens[i+2] should be '{'
      const b = { name, parent: stack.length ? stack[stack.length - 1] : -1,
                  offset: [0, 0, 0], index: bones.length };
      bones.push(b);
      stack.push(b.index);
      i += 3;
    } else if (t === 'offset') {
      const b = bones[stack[stack.length - 1]];
      b.offset = [parseFloat(tokens[i + 1]), parseFloat(tokens[i + 2]),
                  parseFloat(tokens[i + 3])];
      i += 4;
    } else if (t === '}') {
      stack.pop();
      i += 1;
    } else {
      i += 1;   // transX / rotY / Version: / NumBones / numbers -- skip
    }
  }
  return bones;
}

// ------------------------------------------------------- forward kinematics
//
// Per frame: each bone's local transform is translate(offset) * rotate(euler),
// accumulated down the hierarchy. The root additionally gets the clip's root
// translation (channels 0,1,2).
//
// This is done by building a real THREE.Bone hierarchy and letting three.js
// accumulate it, rather than multiplying matrices by hand -- not for elegance,
// but because a THREE.Skeleton over those same Bone objects is what puts the
// character's mesh on the GPU. One set of bones drives both the stick figure
// and the skinned model, so the two cannot drift apart.
const EULER_ORDERS = ['XYZ', 'XZY', 'YXZ', 'YZX', 'ZXY', 'ZYX'];

// The bind pose: each bone's offset accumulated down the hierarchy, with no
// rotation anywhere. That is the whole bind transform in this engine -- it is
// translation-only, which is what rdrstudio/vif.py's to_bind_pose relies on
// (it ADDS the bone's world position to a vertex; it does not multiply). So a
// bone's inverse-bind matrix is just a translation by minus that position.
export function bindPositions(bones) {
  const out = [];
  for (const b of bones) {
    const p = b.parent < 0 ? [0, 0, 0] : out[b.parent];
    out.push([p[0] + b.offset[0], p[1] + b.offset[1], p[2] + b.offset[2]]);
  }
  return out;
}

// ------------------------------------------------- .glb: model + skeleton + clip
//
// glTF 2.0 binary: the skinned mesh (a primitive per texture run, its textures
// embedded as PNG), the skeleton as a node tree in its bind pose (translation
// only, as every rig in this engine), and the clip as one animation, a
// rotation track per bone and a translation track per root. Blender: File >
// Import > glTF 2.0.
function glbFromParts(D) {
  const nb = D.names.length;
  const chunks = [];
  let binLen = 0;
  const views = [], accessors = [];
  function view(bytes, target) {
    const pad = (4 - (binLen % 4)) % 4;
    if (pad) { chunks.push(new Uint8Array(pad)); binLen += pad; }
    const u8 = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    chunks.push(u8);
    const v = { buffer: 0, byteOffset: binLen, byteLength: u8.byteLength };
    if (target) v.target = target;
    binLen += u8.byteLength;
    views.push(v);
    return views.length - 1;
  }
  function accessor(arr, type, ctype, count, target, minmax) {
    const a = { bufferView: view(arr, target), componentType: ctype, count, type };
    if (minmax) { a.min = minmax[0]; a.max = minmax[1]; }
    accessors.push(a);
    return accessors.length - 1;
  }
  const pos = Float32Array.from(D.positions);
  const nv = pos.length / 3;
  const mn = [Infinity, Infinity, Infinity], mx = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < nv; i++) for (let c = 0; c < 3; c++) {
    const v = pos[i * 3 + c]; if (v < mn[c]) mn[c] = v; if (v > mx[c]) mx[c] = v;
  }
  const attrs = { POSITION: accessor(pos, 'VEC3', 5126, nv, 34962, [mn, mx]) };
  if (D.normals) attrs.NORMAL = accessor(Float32Array.from(D.normals), 'VEC3', 5126, nv, 34962);
  if (D.uvs && D.uvs.length === nv * 2) attrs.TEXCOORD_0 = accessor(Float32Array.from(D.uvs), 'VEC2', 5126, nv, 34962);
  const si = D.skinIndex, sw = D.skinWeight;
  const joints = new Uint16Array(nv * 4), weights = new Float32Array(nv * 4);
  for (let i = 0; i < nv; i++) {
    let sum = 0;
    for (let c = 0; c < 4; c++) { joints[i * 4 + c] = Math.min(si[i * 4 + c], nb - 1); sum += sw[i * 4 + c]; }
    for (let c = 0; c < 4; c++) weights[i * 4 + c] = sum > 0 ? sw[i * 4 + c] / sum : (c === 0 ? 1 : 0);
  }
  attrs.JOINTS_0 = accessor(joints, 'VEC4', 5123, nv, 34962);
  attrs.WEIGHTS_0 = accessor(weights, 'VEC4', 5126, nv, 34962);

  const images = [], textures = [], materials = [];
  for (const png of D.pngs.length ? D.pngs : [null]) {
    const mat = { name: 'mat' + materials.length, doubleSided: true,
                  pbrMetallicRoughness: { metallicFactor: 0, roughnessFactor: 1 } };
    if (png && attrs.TEXCOORD_0 !== undefined) {
      images.push({ bufferView: view(png), mimeType: 'image/png' });
      textures.push({ sampler: 0, source: images.length - 1 });
      mat.pbrMetallicRoughness.baseColorTexture = { index: textures.length - 1 };
      mat.alphaMode = 'MASK';
      mat.alphaCutoff = 0.35;
    } else {
      mat.pbrMetallicRoughness.baseColorFactor = [0.78, 0.74, 0.66, 1];
    }
    materials.push(mat);
  }
  const prims = D.groups.filter((g) => g.count > 0).map((g) => ({
    attributes: attrs, mode: 4, material: Math.min(g.materialIndex || 0, materials.length - 1),
    indices: accessor(Uint32Array.from(D.index.subarray(g.start, g.start + g.count)), 'SCALAR', 5125, g.count, 34963),
  }));

  const nodes = D.names.map((n, j) => ({ name: n, translation: Array.from(D.rest[j]).map(Number) }));
  for (let j = 0; j < nb; j++) {
    const p = D.parents[j];
    if (p >= 0) (nodes[p].children = nodes[p].children || []).push(j);
  }
  const ibm = new Float32Array(nb * 16);
  for (let j = 0; j < nb; j++) {
    const o = j * 16;
    ibm[o] = ibm[o + 5] = ibm[o + 10] = ibm[o + 15] = 1;
    ibm[o + 12] = -D.bindW[j][0]; ibm[o + 13] = -D.bindW[j][1]; ibm[o + 14] = -D.bindW[j][2];
  }
  const roots = D.parents.map((p, j) => (p < 0 ? j : -1)).filter((j) => j >= 0);
  const skin = { joints: [...Array(nb).keys()], inverseBindMatrices: accessor(ibm, 'MAT4', 5126, nb), skeleton: roots[0] };
  const meshNode = nodes.length;
  nodes.push({ name: D.meshName || 'model', mesh: 0, skin: 0 });

  const K = D.keys;
  const times = new Float32Array(K);
  for (let k = 0; k < K; k++) times[k] = k / D.rate;
  const input = accessor(times, 'SCALAR', 5126, K, null, [[0], [K > 0 ? times[K - 1] : 0]]);
  const samplers = [], channels = [];
  for (let j = 0; j < nb; j++) {
    samplers.push({ input, output: accessor(D.rot[j], 'VEC4', 5126, K), interpolation: 'LINEAR' });
    channels.push({ sampler: samplers.length - 1, target: { node: j, path: 'rotation' } });
    if (D.parents[j] < 0) {
      samplers.push({ input, output: accessor(D.tra[j], 'VEC3', 5126, K), interpolation: 'LINEAR' });
      channels.push({ sampler: samplers.length - 1, target: { node: j, path: 'translation' } });
    }
  }
  const gltf = {
    asset: { version: '2.0', generator: 'RDR Modding Studio' },
    scene: 0, scenes: [{ nodes: [meshNode, ...roots] }],
    nodes, meshes: [{ name: nodes[meshNode].name, primitives: prims }], skins: [skin],
    animations: [{ name: String(D.clipName || 'clip').replace(/\.[^.]*$/, ''), samplers, channels }],
    materials, buffers: [{ byteLength: 0 }], bufferViews: views, accessors,
  };
  if (images.length) {
    gltf.images = images; gltf.textures = textures;
    gltf.samplers = [{ magFilter: 9729, minFilter: 9987, wrapS: 10497, wrapT: 10497 }];
  }
  const padB = (4 - (binLen % 4)) % 4;
  if (padB) { chunks.push(new Uint8Array(padB)); binLen += padB; }
  gltf.buffers[0].byteLength = binLen;
  let json = new TextEncoder().encode(JSON.stringify(gltf));
  const padJ = (4 - (json.length % 4)) % 4;
  if (padJ) { const j2 = new Uint8Array(json.length + padJ).fill(0x20); j2.set(json); json = j2; }
  const total = 12 + 8 + json.length + 8 + binLen;
  const out = new Uint8Array(total);
  const dv = new DataView(out.buffer);
  dv.setUint32(0, 0x46546C67, true); dv.setUint32(4, 2, true); dv.setUint32(8, total, true);
  dv.setUint32(12, json.length, true); dv.setUint32(16, 0x4E4F534A, true);
  out.set(json, 20);
  let o = 20 + json.length;
  dv.setUint32(o, binLen, true); dv.setUint32(o + 4, 0x004E4942, true);
  o += 8;
  for (const c of chunks) { out.set(c, o); o += c.byteLength; }
  return { bytes: out, bones: nb, keys: K, verts: nv, textures: images.length };
}

// The same .glb without a viewer: one clip on a model, for "Export all
// animations" with the current model. `rig` is the /api/character_rig payload
// the viewer loaded, `pngs` its textures by slot (the same PNGs), `skelText`
// the skeleton the clips play on. Posing is the viewer's: the engine's Euler
// order, root motion on the root, a rig shorter or longer than the clip as the
// engine plays it, and -- for a model on another skeleton -- each of its bones
// taking the rotation of the clip's bone with the same name.
export function bakeClipGlb({ animBuf, skelText, rig, pngs, order = 'YZX', retarget = false, clipName = '' }) {
  const anim = decodeAnim(animBuf);
  const bones = parseSkeleton(skelText);
  const driven = Math.min(bones.length, anim.bones);
  const e = new THREE.Euler(), q = new THREE.Quaternion();
  let names, parents, rest, bindW, map = null;
  if (retarget) {
    const rb = rig.bones;
    names = rb.map((b, j) => String(b.name || 'bone' + j));
    parents = rb.map((b) => (b.parent >= 0 && b.parent < rb.length ? b.parent : -1));
    rest = rb.map((b) => (b.parent >= 0 && b.parent < rb.length
      ? [b.pos[0] - rb[b.parent].pos[0], b.pos[1] - rb[b.parent].pos[1], b.pos[2] - rb[b.parent].pos[2]]
      : b.pos.slice()));
    bindW = rb.map((b) => b.pos);
    const norm = (x) => String(x).toLowerCase().replace(/[^a-z0-9]/g, '');
    const exact = new Map(bones.map((b, i) => [String(b.name).toLowerCase(), i]));
    const loose = new Map(bones.map((b, i) => [norm(b.name), i]));
    map = rb.map((b) => {
      const k = String(b.name).toLowerCase();
      if (exact.has(k)) return exact.get(k);
      return loose.has(norm(b.name)) ? loose.get(norm(b.name)) : -1;
    });
  } else {
    names = bones.map((b, i) => String(b.name || 'bone' + i));
    parents = bones.map((b) => b.parent);
    rest = bones.map((b) => b.offset);
    bindW = bindPositions(bones);
  }
  const nb = names.length, K = anim.keys;
  const rot = names.map(() => new Float32Array(K * 4));
  const tra = names.map(() => new Float32Array(K * 3));
  const clipQ = bones.map(() => new THREE.Quaternion());
  for (let k = 0; k < K; k++) {
    const ch = anim.atKey(k);
    for (let i = 0; i < bones.length; i++) {
      if (i < driven) { e.set(ch[3 + 3 * i], ch[4 + 3 * i], ch[5 + 3 * i], order); clipQ[i].setFromEuler(e); }
      else clipQ[i].identity();
    }
    for (let j = 0; j < nb; j++) {
      const src = map ? map[j] : j;
      if (src >= 0 && src < driven) q.copy(clipQ[src]); else q.identity();
      rot[j].set([q.x, q.y, q.z, q.w], k * 4);
      const r = rest[j];
      if (parents[j] < 0) tra[j].set([r[0] + ch[0], r[1] + ch[1], r[2] + ch[2]], k * 3);
      else tra[j].set([r[0], r[1], r[2]], k * 3);
    }
  }
  // the mesh, as the viewer builds it: groups per texture run, normals from the triangles
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(rig.positions, 3));
  geo.setIndex(rig.indices);
  geo.computeVertexNormals();
  const slots = [];
  for (const g of rig.groups || []) if (!slots.includes(g.texture)) slots.push(g.texture);
  if (!slots.length) slots.push(null);
  const groups = (rig.groups || []).length
    ? rig.groups.map((g) => ({ start: g.start, count: g.count, materialIndex: Math.max(0, slots.indexOf(g.texture)) }))
    : [{ start: 0, count: rig.indices.length, materialIndex: 0 }];
  const idx = Uint32Array.from(rig.indices);
  const out = glbFromParts({
    positions: rig.positions, normals: geo.getAttribute('normal').array,
    uvs: rig.uvs && rig.uvs.length * 3 === rig.positions.length * 2 ? rig.uvs : null,
    skinIndex: rig.skin_index, skinWeight: rig.skin_weight, index: idx, groups,
    pngs: slots.map((sl) => (sl === null || sl === undefined ? null : (pngs || {})[sl] || null)),
    names, parents, rest, bindW, rate: anim.halfRate ? 15 : 30, keys: K, rot, tra,
    meshName: rig.stem ? String(rig.stem) : 'model', clipName,
  });
  geo.dispose();
  return out;
}

// --------------------------------------------------------------- the player
//
// Attaches to a container element, draws the skeleton, and plays. Returns a
// handle with dispose(), setOrder(), play(), pause(), setFrame(), and info.
export function createAnimPlayer(container, animBuf, skelText, opts = {}) {
  const anim = decodeAnim(animBuf);
  const bones = parseSkeleton(skelText);
  // PLAY IT THE WAY THE ENGINE DOES, instead of demanding the counts match.
  //
  // The applier at 0x002D55B8 takes its loop count from the SKELETON: bone i
  // reads channels 3+3i..3+3i+2, so a rig with FEWER bones than the clip
  // drives just stops early, and one with MORE leaves the surplus at rest.
  // Stock data leans on both -- pgj.skel is 52 bones and 252 of its 255 clips
  // drive 53.
  //
  // This used to be `bones.length !== anim.bones -> throw`, which surfaced as
  // "Could not play this clip: this clip needs a 53-bone skeleton but that one
  // has 52". MEASURED over SWAT.DAT: 433 of the 4,166 clips with a 3+3N layout
  // (10.4%) ended there on the ORDINARY path, 405 of them because the picker's
  // default is a near-length rig -- which is the right default, and what the
  // game itself uses. The viewer was the last thing insisting on an exact
  // match, so it turned a correct answer into an error message.
  const driven = Math.min(bones.length, anim.bones);

  let order = opts.order && EULER_ORDERS.includes(opts.order) ? opts.order : 'YZX';

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  const w = container.clientWidth || 480, h = container.clientHeight || 360;
  renderer.setSize(w, h);
  container.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, w / h, 0.01, 1000);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;

  scene.add(new THREE.HemisphereLight(0xffffff, 0x404040, 1.1));
  const grid = new THREE.GridHelper(4, 8, 0x3a4a5a, 0x24303a);
  scene.add(grid);

  // ---- the bones themselves -------------------------------------------
  //
  // One THREE.Bone per skeleton bone, wired into the same hierarchy the .skel
  // declares. Posing sets each bone's local position and rotation; three.js
  // accumulates them, and a Skeleton built over these same objects is what a
  // SkinnedMesh reads. Bind pose is translation-only here, so the inverse-bind
  // matrix of bone i is simply a translation by minus its bind position.
  const bind = bindPositions(bones);
  const boneObjs = bones.map(() => new THREE.Bone());
  const rigRoot = new THREE.Group();
  for (let i = 0; i < bones.length; i++) {
    if (bones[i].parent >= 0) boneObjs[bones[i].parent].add(boneObjs[i]);
    else rigRoot.add(boneObjs[i]);
  }
  scene.add(rigRoot);
  const boneInverses = bind.map((p) =>
    new THREE.Matrix4().makeTranslation(-p[0], -p[1], -p[2]));
  const skeleton = new THREE.Skeleton(boneObjs, boneInverses);

  // Dequantising a key rebuilds the whole channel vector, so do it once per
  // key rather than once per (key, use). A clip is at most a few hundred keys
  // of a ~60-bone skeleton, which is a few hundred KB.
  const chans = [];
  for (let k = 0; k < anim.keys; k++) chans.push(anim.atKey(k));

  const _e = new THREE.Euler();
  const rootDelta = new THREE.Vector3();
  function poseBones(k) {
    const ch = chans[k];
    rootDelta.set(ch[0], ch[1], ch[2]);
    for (let i = 0; i < bones.length; i++) {
      const b = bones[i], o = boneObjs[i], base = 3 + 3 * i;
      // Past `driven` the clip has nothing to say about this bone -- either it
      // is a surplus bone on a longer rig, or the clip ran out. Leave it at
      // its rest pose, which is what the engine's apply loop does.
      if (i < driven) {
        _e.set(ch[base], ch[base + 1], ch[base + 2], order);
        o.quaternion.setFromEuler(_e);
      } else {
        o.quaternion.identity();
      }
      if (b.parent < 0)
        o.position.set(b.offset[0] + ch[0], b.offset[1] + ch[1],
                       b.offset[2] + ch[2]);
      else o.position.set(b.offset[0], b.offset[1], b.offset[2]);
    }
    rigRoot.updateMatrixWorld(true);
  }

  // Where every joint goes on every key, for the stick figure and for framing
  // the camera. Cheap, and it makes scrubbing instant.
  let frames = [];
  function rebuild() {
    frames = [];
    for (let k = 0; k < anim.keys; k++) {
      poseBones(k);
      frames.push(boneObjs.map((o) =>
        new THREE.Vector3().setFromMatrixPosition(o.matrixWorld)));
    }
  }
  rebuild();

  // Frame the skeleton from its overall extent across the whole clip.
  const box = new THREE.Box3();
  for (const f of frames) for (const p of f) box.expandByPoint(p);
  const c = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3()).length() || 2;
  function frameCamera(target, extent) {
    controls.target.copy(target);
    camera.position.set(target.x, target.y + extent * 0.1, target.z + extent * 1.3);
    controls.update();
  }
  frameCamera(c, size);
  grid.position.y = box.min.y;

  // DESIGN-REVIEW B8: frame on what is actually drawn -- the posed, skinned
  // model where there is one, unioned with the skeleton's whole-clip extent --
  // and sit the grid under its feet. Called once a model first appears, and
  // by F at any time.
  function reframe() {
    const b = box.clone();
    if (mesh && mesh.visible) {
      try { b.union(new THREE.Box3().setFromObject(mesh, true)); } catch {}
    }
    if (b.isEmpty()) return;
    grid.position.y = b.min.y;
    frameCamera(b.getCenter(new THREE.Vector3()),
                b.getSize(new THREE.Vector3()).length() || size);
    renderNow();
  }

  // Bones as line segments (parent -> child), joints as small dots.
  const segCount = bones.filter((b) => b.parent >= 0).length;
  const lineGeo = new THREE.BufferGeometry();
  const linePos = new Float32Array(segCount * 2 * 3);
  lineGeo.setAttribute('position', new THREE.BufferAttribute(linePos, 3));
  const lines = new THREE.LineSegments(lineGeo,
    new THREE.LineBasicMaterial({ color: 0xffcf6b }));
  scene.add(lines);

  const dotGeo = new THREE.BufferGeometry();
  const dotPos = new Float32Array(bones.length * 3);
  dotGeo.setAttribute('position', new THREE.BufferAttribute(dotPos, 3));
  const dots = new THREE.Points(dotGeo,
    new THREE.PointsMaterial({ color: 0x8fd0ff, size: size * 0.02 }));
  scene.add(dots);

  // ---- the character's own model, skinned to those bones ----------------
  //
  // TRUE per-vertex linear blend skinning, not attachment: every vertex
  // carries up to four (bone, weight) pairs out of the display list and the
  // GPU blends them. The one exception is stated on screen -- a face and a hat
  // carry no per-vertex bone at all (they are rigid meshes authored in
  // head-local space), so the server gives them a single full-weight influence
  // on the head bone. That part IS rigid attachment and is counted separately.
  let mesh = null, meshInfo = null, framedOnce = false;
  const meshTextures = [];

  // A model on ANOTHER skeleton than the clip's (retargeted by bone name): its
  // own bones, each copying the rotation of the clip's bone of the same name.
  let ret = null;

  function poseRetarget() {
    if (!ret) return;
    for (let j = 0; j < ret.objs.length; j++) {
      const i = ret.map[j], o = ret.objs[j], L = ret.local[j];
      if (i >= 0 && i < driven) o.quaternion.copy(boneObjs[i].quaternion);
      else o.quaternion.identity();
      if (ret.rb[j].parent < 0) o.position.set(L[0] + rootDelta.x, L[1] + rootDelta.y, L[2] + rootDelta.z);
      else o.position.set(L[0], L[1], L[2]);
    }
    ret.root.updateMatrixWorld(true);
  }

  function clearModel() {
    if (ret) { scene.remove(ret.root); ret.skel.dispose(); ret = null; }
    if (!mesh) return;
    scene.remove(mesh);
    mesh.geometry.dispose();
    for (const m of [].concat(mesh.material)) m.dispose();
    for (const t of meshTextures.splice(0)) t.dispose();
    mesh = null;
    meshInfo = null;
  }

  // `rig` is the /api/character_rig payload; `textureUrl(slot)` turns a
  // texture slot into a URL, so this module needs to know nothing about the
  // server's route names.
  function setModel(rig, textureUrl, mopts = {}) {
    clearModel();
    const retarget = !!mopts.retarget;
    if (!rig) { applyShow(); return null; }
    // A model may be rigged to MORE bones than the skeleton we are posing --
    // its extra joints simply never move. What it must not do is reference a
    // bone index this skeleton does not have, because there would be no matrix
    // to skin against; that is checked per index below, where it is a fact
    // rather than a guess from two lengths.
    if (!rig.bones || !rig.bones.length)
      throw new Error('that model came back without a skeleton to skin it to');
    if (!rig.skin_index || !rig.skin_weight)
      throw new Error('that model came back without per-vertex bone weights, '
        + 'so there is nothing to skin it with');
    // The real requirement, checked as a fact rather than inferred from two
    // bone counts: every index the mesh names must exist on the rig we are
    // posing. A model with MORE bones is fine -- the surplus never moves.
    let worst = -1;
    for (let i = 0; i < rig.skin_index.length; i++)
      if (rig.skin_index[i] > worst) worst = rig.skin_index[i];
    if (!retarget && worst >= bones.length)
      throw new Error('that model skins to bone ' + worst + ', and the '
        + 'skeleton playing this clip only has ' + bones.length
        + '. Pick a rig with at least ' + (worst + 1) + ' bones.');

    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position',
      new THREE.Float32BufferAttribute(rig.positions, 3));
    if (rig.uvs && rig.uvs.length * 3 === rig.positions.length * 2)
      geo.setAttribute('uv', new THREE.Float32BufferAttribute(rig.uvs, 2));
    geo.setAttribute('skinIndex',
      new THREE.Uint16BufferAttribute(rig.skin_index, 4));
    geo.setAttribute('skinWeight',
      new THREE.Float32BufferAttribute(rig.skin_weight, 4));
    geo.setIndex(rig.indices);
    geo.computeVertexNormals();

    // One material per texture the character actually uses, and a draw group
    // per surface run. 37 of the 310 character bundles in SWAT.DAT carry more
    // than one texture -- Red's body is on his SECOND one, 1,872 of his 2,503
    // LOD-0 triangles -- so a single atlas would put the wrong picture on most
    // of him.
    const slots = [];
    for (const g of rig.groups || []) if (!slots.includes(g.texture)) slots.push(g.texture);
    if (!slots.length) slots.push(null);
    const mats = slots.map((slot) => {
      if (slot === null || slot === undefined || !geo.getAttribute('uv'))
        return new THREE.MeshStandardMaterial(
          { color: 0xc8bda8, roughness: 0.85, side: THREE.DoubleSide });
      const tex = new THREE.TextureLoader().load(textureUrl(slot));
      tex.colorSpace = THREE.SRGBColorSpace;
      tex.flipY = false;
      tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
      meshTextures.push(tex);
      return new THREE.MeshBasicMaterial(
        { map: tex, side: THREE.DoubleSide, transparent: true, alphaTest: 0.35 });
    });
    for (const g of rig.groups || [])
      geo.addGroup(g.start, g.count, Math.max(0, slots.indexOf(g.texture)));
    if (!(rig.groups || []).length) geo.addGroup(0, rig.indices.length, 0);

    mesh = new THREE.SkinnedMesh(geo, mats.length === 1 ? mats[0] : mats);
    // The bind matrix is identity because the positions arriving here are
    // already in the skeleton's own space; passing it explicitly stops
    // three.js recomputing the inverse-bind matrices from the CURRENT pose,
    // which would bind the character to whatever frame happened to be up.
    let matched = 0;
    if (retarget) {
      // The model keeps its OWN skeleton (bind pose: its bones' positions, no
      // rotation, as every rig in this engine), and each of its bones takes
      // the rotation of the clip's bone with the same name: exact first, then
      // ignoring _ and case (spine01 = spine_01). Bones with no match stay at
      // rest. Hair, tails and extra joints therefore hang still, and a rig
      // whose bones sit in another ORDER (ani's hair bones come before its
      // arms) is still driven joint for joint.
      const rb = rig.bones;
      if (worst >= rb.length) throw new Error('that model skins to bone ' + worst + ' and its skeleton has ' + rb.length);
      const objs = rb.map(() => new THREE.Bone());
      const root = new THREE.Group();
      rb.forEach((b, j) => {
        if (b.parent >= 0 && b.parent < rb.length) objs[b.parent].add(objs[j]); else root.add(objs[j]);
      });
      const local = rb.map((b) => (b.parent >= 0 && b.parent < rb.length
        ? [b.pos[0] - rb[b.parent].pos[0], b.pos[1] - rb[b.parent].pos[1], b.pos[2] - rb[b.parent].pos[2]]
        : b.pos.slice()));
      scene.add(root);
      const skel2 = new THREE.Skeleton(objs,
        rb.map((b) => new THREE.Matrix4().makeTranslation(-b.pos[0], -b.pos[1], -b.pos[2])));
      const norm = (x) => String(x).toLowerCase().replace(/[^a-z0-9]/g, '');
      const exact = new Map(bones.map((b, i) => [String(b.name).toLowerCase(), i]));
      const loose = new Map(bones.map((b, i) => [norm(b.name), i]));
      const map = rb.map((b) => {
        const k = String(b.name).toLowerCase();
        if (exact.has(k)) return exact.get(k);
        return loose.has(norm(b.name)) ? loose.get(norm(b.name)) : -1;
      });
      matched = map.filter((i) => i >= 0 && i < driven).length;
      ret = { objs, root, skel: skel2, map, local, rb };
      poseRetarget();
      mesh.bind(skel2, new THREE.Matrix4());
    } else {
      mesh.bind(skeleton, new THREE.Matrix4());
    }
    // An animated pose reaches outside the bind-pose bounding sphere, and a
    // culled character reads as "the model option does nothing".
    mesh.frustumCulled = false;
    scene.add(mesh);

    // How far the model's own rig sits from the skeleton being played. Zero
    // when they are the same file, which is the normal case; non-zero means
    // the clip is on a same-size skeleton from a DIFFERENT character and the
    // joints will not line up. Reported, never silently absorbed.
    let drift = 0;
    for (let i = 0; i < (retarget ? 0 : bones.length); i++) {
      const p = rig.bones[i].pos;
      drift = Math.max(drift, Math.abs(p[0] - bind[i][0]),
                       Math.abs(p[1] - bind[i][1]), Math.abs(p[2] - bind[i][2]));
    }
    meshInfo = { stem: rig.stem, verts: rig.verts, tris: rig.tris,
                 rigid: rig.rigid_verts || 0, textures: (rig.textures || []).length,
                 skel: rig.skel, drift, parts: rig.parts || [],
                 retarget: retarget ? { matched, total: rig.bones.length } : null };
    applyShow();

    // Refit the camera the FIRST time a model appears -- a skeleton's extent
    // stops at the top joint, so framing on it alone cuts the head and the hat
    // off. Not on later swaps: comparing two characters on one motion means
    // keeping the angle you had chosen to compare them from.
    if (!framedOnce) {
      framedOnce = true;
      reframe();
    }
    renderNow();
    return meshInfo;
  }

  // ---- the clip on the model, as one .glb Blender opens animated ---------
  //
  // Baked key by key from the SAME posing the viewer draws -- Euler order,
  // root motion, retargeting by name -- so what Blender plays is what was on
  // screen. The file itself is written by glbFromParts (below the player),
  // which "Export all animations" with the current model shares.
  async function exportGlb(clipName) {
    if (!mesh) throw new Error('load a character model first');
    const R = ret
      ? { objs: ret.objs, parents: ret.rb.map((b) => (b.parent >= 0 && b.parent < ret.rb.length ? b.parent : -1)),
          rest: ret.local, bindW: ret.rb.map((b) => b.pos), names: ret.rb.map((b, j) => String(b.name || 'bone' + j)) }
      : { objs: boneObjs, parents: bones.map((b) => b.parent), rest: bones.map((b) => b.offset),
          bindW: bind, names: bones.map((b, i) => String(b.name || 'bone' + i)) };
    const K = anim.keys, nb = R.objs.length;
    const rot = R.objs.map(() => new Float32Array(K * 4));
    const tra = R.objs.map(() => new Float32Array(K * 3));
    for (let k = 0; k < K; k++) {
      poseBones(k);
      poseRetarget();
      for (let j = 0; j < nb; j++) {
        const q = R.objs[j].quaternion, t = R.objs[j].position;
        rot[j].set([q.x, q.y, q.z, q.w], k * 4);
        tra[j].set([t.x, t.y, t.z], k * 3);
      }
    }
    renderNow();                        // back to the frame on screen
    const geo = mesh.geometry;
    const pngs = [];
    for (const m of [].concat(mesh.material)) {
      const src = m.map && m.map.image && (m.map.image.currentSrc || m.map.image.src);
      let png = null;
      if (src) { try { png = new Uint8Array(await (await fetch(src)).arrayBuffer()); } catch { png = null; } }
      pngs.push(png);
    }
    const idx = geo.getIndex().array;
    return glbFromParts({
      positions: geo.getAttribute('position').array, normals: geo.getAttribute('normal')?.array,
      uvs: geo.getAttribute('uv')?.array, skinIndex: geo.getAttribute('skinIndex').array,
      skinWeight: geo.getAttribute('skinWeight').array, index: idx,
      groups: geo.groups.length ? geo.groups : [{ start: 0, count: idx.length, materialIndex: 0 }],
      pngs, names: R.names, parents: R.parents, rest: R.rest, bindW: R.bindW,
      rate: anim.halfRate ? 15 : 30, keys: K, rot, tra,
      meshName: meshInfo && meshInfo.stem ? String(meshInfo.stem) : 'model', clipName,
    });
  }

  let show = opts.show === 'model' || opts.show === 'both' ? opts.show : 'skeleton';
  function applyShow() {
    const wantMesh = show === 'model' || show === 'both';
    const wantBones = show === 'skeleton' || show === 'both' || !mesh;
    if (mesh) mesh.visible = wantMesh;
    lines.visible = wantBones;
    dots.visible = wantBones;
  }
  applyShow();

  function drawFrame(k) {
    poseBones(k);
    poseRetarget();
    const f = frames[k];
    let s = 0;
    for (const b of bones) {
      dotPos[b.index * 3] = f[b.index].x;
      dotPos[b.index * 3 + 1] = f[b.index].y;
      dotPos[b.index * 3 + 2] = f[b.index].z;
      if (b.parent >= 0) {
        const a = f[b.parent], q = f[b.index];
        linePos[s++] = a.x; linePos[s++] = a.y; linePos[s++] = a.z;
        linePos[s++] = q.x; linePos[s++] = q.y; linePos[s++] = q.z;
      }
    }
    lineGeo.attributes.position.needsUpdate = true;
    dotGeo.attributes.position.needsUpdate = true;
  }

  // Draw one frame and paint it now, independent of the animation loop. The
  // loop drives playback, but scrubbing, the first frame, and any environment
  // that throttles requestAnimationFrame (a background browser tab does) all
  // need a frame to appear on demand.
  function renderNow() {
    drawFrame(frame);
    controls.update();
    renderer.render(scene, camera);
  }

  let frame = 0, playing = true, last = performance.now(), acc = 0;
  // Keys are stored at half rate for half-rate clips, so the on-screen key rate
  // is 15/s there and 30/s otherwise -- matching the game's own timing.
  const keyRate = anim.halfRate ? 15 : 30;
  const onFrame = opts.onFrame || (() => {});

  let raf = 0;
  function loop(now) {
    raf = requestAnimationFrame(loop);
    const dt = (now - last) / 1000; last = now;
    if (playing) {
      acc += dt * keyRate;
      while (acc >= 1) {
        acc -= 1;
        frame = (frame + 1) % anim.keys;
        onFrame(frame);
      }
    }
    drawFrame(frame);
    controls.update();
    renderer.render(scene, camera);
  }
  raf = requestAnimationFrame(loop);
  renderNow();   // show the first pose immediately, before the loop's first tick

  function onResize() {
    const ww = container.clientWidth || w, hh = container.clientHeight || h;
    renderer.setSize(ww, hh);
    camera.aspect = ww / hh;
    camera.updateProjectionMatrix();
  }
  addEventListener('resize', onResize);

  return {
    info: { magic: anim.magic, frames: anim.frames, keys: anim.keys,
            bones: bones.length, channels: anim.channels, loop: anim.loop,
            halfRate: anim.halfRate, keyRate },
    orders: EULER_ORDERS,
    get order() { return order; },
    setOrder(o) { if (EULER_ORDERS.includes(o)) { order = o; rebuild(); } },
    // The character's model, skinned to these same bones. Returns what it
    // measured about the model, or null when cleared.
    setModel(rig, textureUrl, mopts) { return setModel(rig, textureUrl, mopts); },
    clearModel() { clearModel(); applyShow(); renderNow(); },
    get modelInfo() { return meshInfo; },
    // The clip on the current model as a .glb (Blender: File > Import > glTF 2.0).
    exportGlb(clipName) { return exportGlb(clipName); },
    get show() { return show; },
    setShow(v) {
      show = ['model', 'both', 'skeleton'].includes(v) ? v : 'skeleton';
      applyShow();
      renderNow();
    },
    reframe() { reframe(); },
    play() { playing = true; },
    pause() { playing = false; },
    get playing() { return playing; },
    get frame() { return frame; },
    setFrame(k) {
      frame = Math.max(0, Math.min(anim.keys - 1, k | 0));
      acc = 0;
      renderNow();
    },
    dispose() {
      cancelAnimationFrame(raf);
      removeEventListener('resize', onResize);
      // A character is a few thousand vertices plus up to nine 256x256
      // textures, and this player is torn down and rebuilt on every click in
      // the clip list -- so the model's GPU memory has to go back explicitly.
      clearModel();
      skeleton.dispose();
      lineGeo.dispose();
      dotGeo.dispose();
      controls.dispose();
      renderer.dispose();
      if (renderer.domElement.parentNode)
        renderer.domElement.parentNode.removeChild(renderer.domElement);
    },
  };
}
