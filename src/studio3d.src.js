/* استوديو نسيم 3D: المبنى بعد التشطيب — لف حواليه، ادخل جواه، افرش، حرّك، وغيّر الألوان.
   Built from the conceptual layout (rooms as rectangles per floor). Bundled with three.js by esbuild into ../studio3d.js. */
import * as THREE from "three";

const H = 3.0, SLAB = 0.2, WI = 0.1, DOOR_W = 0.95, DOOR_H = 2.15, EYE = 1.6;
const PALETTE = ["#f4f1ea", "#e9e2d0", "#d9cbb0", "#c7d3c0", "#b9cfd8", "#d8c3c3", "#e6d5b8", "#a7b4c2", "#8f8f8f", "#5b4a3f", "#2f3b45", "#ffffff",
  "#c49a6c", "#8b5a2b", "#3c6e71", "#d1495b", "#edae49", "#00798c", "#30638e", "#003d5b", "#6b4226", "#e0e0e0", "#bfa07a", "#4f6d7a"];
const FLOORS = ["#d8cdb9", "#bfae94", "#e8e3da", "#9c8b76", "#cfc7bd", "#7d6a58", "#eeeeee", "#b7b0a6"];

/* ---------- small helpers ---------- */
const el = (tag, attrs = {}, ...kids) => { const e = document.createElement(tag); for (const [k, v] of Object.entries(attrs)) { if (k === "style") e.style.cssText = v; else if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else if (v != null) e.setAttribute(k, v); } for (const c of kids.flat()) if (c != null) e.append(c); return e; };
const mat = (color, o = {}) => new THREE.MeshStandardMaterial({ color, roughness: o.r ?? 0.85, metalness: o.m ?? 0, transparent: !!o.t, opacity: o.t ?? 1 });
function box(w, h, d, m, x = 0, y = 0, z = 0) { const g = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), m); g.position.set(x, y, z); g.castShadow = true; g.receiveShadow = true; return g; }
function cyl(r, h, m, x = 0, y = 0, z = 0) { const g = new THREE.Mesh(new THREE.CylinderGeometry(r, r, h, 16), m); g.position.set(x, y, z); g.castShadow = true; return g; }
let texCache = {};
function tileTexture(color, size = 0.6) {
  const k = color + size; if (texCache[k]) return texCache[k];
  const c = document.createElement("canvas"); c.width = c.height = 128; const x = c.getContext("2d");
  x.fillStyle = color; x.fillRect(0, 0, 128, 128); x.strokeStyle = "rgba(0,0,0,.18)"; x.lineWidth = 2; x.strokeRect(1, 1, 126, 126);
  const t = new THREE.CanvasTexture(c); t.wrapS = t.wrapT = THREE.RepeatWrapping; t.colorSpace = THREE.SRGBColorSpace; t.userData.size = size; texCache[k] = t; return t;
}

/* ---------- furniture catalog (meters; origin at floor center of the item) ---------- */
const CATALOG = {
  sofa: { ar: "كنبة", w: 2.1, d: 0.9, color: "#6d7f8f", make: (m) => [box(2.1, 0.42, 0.9, m, 0, 0.21, 0), box(2.1, 0.45, 0.2, m, 0, 0.62, -0.35), box(0.2, 0.25, 0.9, m, -0.95, 0.55, 0), box(0.2, 0.25, 0.9, m, 0.95, 0.55, 0)] },
  armchair: { ar: "فوتيه", w: 0.9, d: 0.85, color: "#8a6f5a", make: (m) => [box(0.9, 0.42, 0.85, m, 0, 0.21, 0), box(0.9, 0.45, 0.18, m, 0, 0.62, -0.33), box(0.15, 0.25, 0.85, m, -0.37, 0.55, 0), box(0.15, 0.25, 0.85, m, 0.37, 0.55, 0)] },
  coffee: { ar: "ترابيزة صالون", w: 1.1, d: 0.6, color: "#5b4a3f", make: (m) => [box(1.1, 0.06, 0.6, m, 0, 0.42, 0), box(0.06, 0.4, 0.06, m, -0.5, 0.2, -0.25), box(0.06, 0.4, 0.06, m, 0.5, 0.2, -0.25), box(0.06, 0.4, 0.06, m, -0.5, 0.2, 0.25), box(0.06, 0.4, 0.06, m, 0.5, 0.2, 0.25)] },
  tv: { ar: "وحدة تلفزيون", w: 1.8, d: 0.45, color: "#3a3a3a", make: (m) => [box(1.8, 0.45, 0.45, m, 0, 0.225, 0), box(1.3, 0.75, 0.06, mat("#111", { r: 0.3 }), 0, 0.95, -0.05)] },
  dining: { ar: "سفرة 6 كراسي", w: 1.8, d: 1.9, color: "#7b5a3c", make: (m) => { const t = [box(1.6, 0.06, 0.9, m, 0, 0.75, 0)]; for (const [x, z] of [[-0.7, -0.4], [0.7, -0.4], [-0.7, 0.4], [0.7, 0.4]]) t.push(box(0.06, 0.72, 0.06, m, x, 0.36, z)); for (const x of [-0.5, 0, 0.5]) for (const s of [-1, 1]) { t.push(box(0.42, 0.05, 0.42, m, x, 0.46, s * 0.72), box(0.42, 0.5, 0.05, m, x, 0.72, s * 0.92)); } return t; } },
  bed2: { ar: "سرير مزدوج", w: 1.8, d: 2.1, color: "#e8e1d4", make: (m) => [box(1.8, 0.35, 2.0, mat("#6b4f3a"), 0, 0.175, 0.05), box(1.7, 0.2, 1.9, m, 0, 0.45, 0.05), box(1.8, 1.0, 0.1, mat("#6b4f3a"), 0, 0.5, -1.0), box(0.6, 0.12, 0.35, mat("#fff"), -0.42, 0.6, -0.7), box(0.6, 0.12, 0.35, mat("#fff"), 0.42, 0.6, -0.7)] },
  bed1: { ar: "سرير فردي", w: 1.0, d: 2.05, color: "#dfe7ef", make: (m) => [box(1.0, 0.35, 2.0, mat("#7a5c45"), 0, 0.175, 0.03), box(0.92, 0.2, 1.9, m, 0, 0.45, 0.03), box(1.0, 0.9, 0.08, mat("#7a5c45"), 0, 0.45, -1.0), box(0.6, 0.12, 0.35, mat("#fff"), 0, 0.6, -0.7)] },
  wardrobe: { ar: "دولاب", w: 1.8, d: 0.6, color: "#bfa07a", make: (m) => [box(1.8, 2.2, 0.6, m, 0, 1.1, 0), box(0.02, 2.0, 0.01, mat("#555"), 0, 1.1, 0.305)] },
  night: { ar: "كومودينو", w: 0.5, d: 0.4, color: "#bfa07a", make: (m) => [box(0.5, 0.5, 0.4, m, 0, 0.25, 0), cyl(0.12, 0.3, mat("#f3e3b0"), 0, 0.65, 0)] },
  desk: { ar: "مكتب", w: 1.3, d: 0.65, color: "#8b6b4a", make: (m) => [box(1.3, 0.05, 0.65, m, 0, 0.75, 0), box(0.05, 0.73, 0.6, m, -0.6, 0.365, 0), box(0.05, 0.73, 0.6, m, 0.6, 0.365, 0), box(0.45, 0.05, 0.45, mat("#333"), 0, 0.46, 0.55), box(0.45, 0.5, 0.05, mat("#333"), 0, 0.72, 0.77)] },
  kitchen: { ar: "مطبخ (دواليب + رخامة)", w: 2.6, d: 0.62, color: "#e9e2d0", make: (m) => [box(2.6, 0.86, 0.6, m, 0, 0.43, 0), box(2.62, 0.04, 0.64, mat("#2f2f2f", { r: 0.3 }), 0, 0.88, 0), box(2.6, 0.7, 0.35, m, 0, 1.85, -0.13), box(0.6, 0.02, 0.45, mat("#999", { m: 0.6, r: 0.3 }), 0.6, 0.91, 0)] },
  fridge: { ar: "تلاجة", w: 0.75, d: 0.7, color: "#d9d9d9", make: (m) => [box(0.75, 1.85, 0.7, mat(m.color, { r: 0.3, m: 0.3 }), 0, 0.925, 0)] },
  stove: { ar: "بوتاجاز", w: 0.6, d: 0.6, color: "#bcbcbc", make: (m) => [box(0.6, 0.88, 0.6, mat(m.color, { r: 0.4, m: 0.4 }), 0, 0.44, 0), box(0.6, 0.02, 0.6, mat("#222"), 0, 0.89, 0)] },
  toilet: { ar: "تواليت", w: 0.4, d: 0.65, color: "#ffffff", make: (m) => [box(0.38, 0.4, 0.5, mat("#fff", { r: 0.2 }), 0, 0.2, 0.05), box(0.4, 0.4, 0.18, mat("#fff", { r: 0.2 }), 0, 0.6, -0.22)] },
  sink: { ar: "حوض", w: 0.6, d: 0.45, color: "#ffffff", make: (m) => [box(0.6, 0.16, 0.45, mat("#fff", { r: 0.2 }), 0, 0.82, 0), cyl(0.06, 0.74, mat("#fff"), 0, 0.37, 0), box(0.5, 0.7, 0.03, mat("#9fb7c2", { r: 0.05, m: 0.6 }), 0, 1.45, -0.2)] },
  shower: { ar: "شاور", w: 0.9, d: 0.9, color: "#cfe3ea", make: (m) => [box(0.9, 0.08, 0.9, mat("#fff"), 0, 0.04, 0), box(0.9, 2.0, 0.02, mat(m.color, { t: 0.35, r: 0.05 }), 0, 1.04, 0.44), box(0.02, 2.0, 0.9, mat(m.color, { t: 0.35, r: 0.05 }), 0.44, 1.04, 0)] },
  rug: { ar: "سجادة", w: 2.0, d: 1.4, color: "#a35d4a", make: (m) => [box(2.0, 0.015, 1.4, m, 0, 0.008, 0)] },
  plant: { ar: "زرع", w: 0.5, d: 0.5, color: "#3f7d3a", make: (m) => [cyl(0.18, 0.4, mat("#c27a4a"), 0, 0.2, 0), new THREE.Mesh(new THREE.SphereGeometry(0.35, 12, 10), m)].map((g, i) => { if (i === 1) { g.position.y = 0.75; g.castShadow = true; } return g; }) },
  lamp: { ar: "أباجورة", w: 0.4, d: 0.4, color: "#f3e3b0", make: (m) => [cyl(0.03, 1.5, mat("#444"), 0, 0.75, 0), cyl(0.2, 0.3, m, 0, 1.55, 0)] },
  shelf: { ar: "مكتبة", w: 1.0, d: 0.35, color: "#8b6b4a", make: (m) => { const a = [box(1.0, 2.0, 0.04, m, 0, 1.0, -0.15), box(0.04, 2.0, 0.35, m, -0.48, 1.0, 0), box(0.04, 2.0, 0.35, m, 0.48, 1.0, 0)]; for (let y = 0.05; y < 2; y += 0.48) a.push(box(1.0, 0.03, 0.35, m, 0, y, 0)); return a; } },
  car: { ar: "عربية", w: 1.8, d: 4.4, color: "#30638e", make: (m) => [box(1.8, 0.7, 4.4, mat(m.color, { r: 0.3, m: 0.5 }), 0, 0.55, 0), box(1.6, 0.55, 2.2, mat("#223", { t: 0.7, r: 0.1 }), 0, 1.15, -0.2), ...[[-0.8, -1.4], [0.8, -1.4], [-0.8, 1.4], [0.8, 1.4]].map(([x, z]) => { const w = cyl(0.33, 0.25, mat("#111"), x, 0.33, z); w.rotation.z = Math.PI / 2; return w; })] },
};

/* ---------- which furniture goes in which room ---------- */
function autoFurnish(room) {
  const n = room.name, W = room.w, D = room.h, out = [], add = (type, x, z, rot = 0) => out.push({ type, x: room.x + W / 2 + x, z: room.y + D / 2 + z, rot });
  const back = -D / 2 + 0.15, front = D / 2 - 0.15, left = -W / 2 + 0.15, right = W / 2 - 0.15;
  if (/سلم|درج/.test(n)) return out;
  if (/حمام|توال/.test(n)) { add("toilet", left + 0.3, back + 0.35); add("sink", right - 0.35, back + 0.25); if (W * D > 4) add("shower", right - 0.5, front - 0.5); return out; }
  if (/مطبخ/.test(n)) { add("kitchen", Math.min(0, right - 1.3 - left - 1.3) / 2, back + 0.3); if (W > 2.2) add("fridge", right - 0.4, front - 0.45, Math.PI); if (D > 2.6) add("stove", left + 0.35, front - 0.35, Math.PI); return out; }
  if (/نوم|ماستر|اوض|أوض|غرف/.test(n) && !/معيش|سفر/.test(n)) {
    const dbl = /ماستر|رئيس/.test(n) || W * D >= 12; add(dbl ? "bed2" : "bed1", 0, back + 1.05);
    if (dbl && W > 2.8) { add("night", -1.2, back + 0.25); add("night", 1.2, back + 0.25); }
    if (D > 3.2) add("wardrobe", 0, front - 0.3, Math.PI); else if (W > 3) add("wardrobe", right - 0.3, 0, -Math.PI / 2);
    if (/اطفال|أطفال|مكتب/.test(n) && W > 3) add("desk", left + 0.7, front - 0.4, Math.PI);
    return out;
  }
  if (/سفر/.test(n)) { add("dining", 0, 0); return out; }
  if (/مكتب|مذاكر/.test(n)) { add("desk", 0, back + 0.4); add("shelf", right - 0.2, 0, -Math.PI / 2); return out; }
  if (/جراج/.test(n)) { add("car", 0, 0); return out; }
  if (/ريسبشن|صال|معيش|استقبال|ليفنج/.test(n)) {
    const dine = W * D > 30 && W >= 6, ox = dine ? -(W / 2 - 1.6) + 1.05 - 0.2 : 0;   // living corner on one side, dining on the other
    add("rug", ox, 0); add("sofa", ox, Math.min(1.4, D / 2 - 0.6), Math.PI); add("coffee", ox, 0.1); add("tv", ox, back + 0.25);
    if (W > 4) add("armchair", ox - Math.min(1.7, W / 2 - 0.6), 0, Math.PI / 2);
    if (W > 4 && !dine) add("armchair", Math.min(1.7, W / 2 - 0.6), 0, -Math.PI / 2);
    if (dine) add("dining", right - 1.0, 0, Math.PI / 2);
    add("plant", right - 0.3, front - 0.3);
    return out;
  }
  return out;
}

/* ---------- the studio ---------- */
export function open(opts) {
  const { layout, title, onSave } = opts;   // layout: {W, L, floors:[{name, rooms:[{name,x,y,w,h}]}]}
  const state = JSON.parse(JSON.stringify(opts.scene || {})); state.colors = state.colors || {}; state.facade = state.facade || "#e6d5b8";
  const firstTime = !state.furniture; if (firstTime) { state.furniture = []; layout.floors.forEach((f, fi) => f.rooms.forEach((r) => autoFurnish(r).forEach((it) => state.furniture.push({ id: Math.random().toString(36).slice(2, 9), floor: fi, ...it, color: CATALOG[it.type].color })))); }
  const save = () => { try { onSave && onSave(JSON.parse(JSON.stringify(state))); } catch (e) {} };

  /* DOM */
  const css = `.s3d{position:fixed;inset:0;z-index:9999;background:#cfe2ee;font-family:inherit;direction:rtl;touch-action:none;user-select:none}
  .s3d canvas{display:block;width:100%;height:100%}
  .s3d .bar{position:absolute;top:0;left:0;right:0;display:flex;gap:6px;flex-wrap:wrap;padding:8px;background:linear-gradient(#0f1b24ee,#0f1b2400);color:#fff;align-items:center}
  .s3d button,.s3d select{font:inherit;font-size:14px;border:0;border-radius:10px;padding:8px 10px;background:#ffffffe6;color:#123;cursor:pointer}
  .s3d button.on{background:#0e7490;color:#fff}
  .s3d .title{font-weight:700;margin-inline-end:auto;font-size:15px}
  .s3d .panel{position:absolute;left:8px;right:8px;bottom:8px;background:#0f1b24ee;color:#fff;border-radius:14px;padding:10px;display:none;max-height:45%;overflow:auto}
  .s3d .panel.show{display:block}
  .s3d .sw{width:30px;height:30px;border-radius:8px;border:2px solid #fff;display:inline-block;margin:3px;padding:0}
  .s3d .grid{display:flex;flex-wrap:wrap;gap:6px}
  .s3d .hint{position:absolute;bottom:10px;left:50%;transform:translateX(-50%);background:#0f1b24cc;color:#fff;padding:6px 12px;border-radius:20px;font-size:13px;pointer-events:none;white-space:nowrap}
  .s3d .stick{position:absolute;bottom:24px;left:24px;width:110px;height:110px;border-radius:50%;background:#ffffff33;border:2px solid #ffffff88;display:none}
  .s3d .stick i{position:absolute;left:35px;top:35px;width:40px;height:40px;border-radius:50%;background:#fffc}
  .s3d.walk .stick{display:block}`;
  const root = el("div", { class: "s3d", role: "dialog", "aria-label": "مجسم 3D" }, el("style", {}, css));
  if (opts.headless) root.style.cssText = "position:fixed;left:-10000px;top:0;width:1280px;height:720px;inset:auto;pointer-events:none";
  const canvas = el("canvas", { "aria-label": "المجسم" });
  const hint = el("div", { class: "hint" }, "اسحب عشان تلف • قرّب بصباعين • دوس على أي حاجة عشان تختارها");
  const stick = el("div", { class: "stick" }, el("i"));
  const panel = el("div", { class: "panel" });
  const btnOrbit = el("button", { class: "on" }, "🔄 حوالين");
  const btnWalk = el("button", {}, "🚶 ادخل جوه");
  const floorSel = el("select", { "aria-label": "الدور" }, el("option", { value: "all" }, "المبنى كله"), ...layout.floors.map((f, i) => el("option", { value: String(i) }, f.name)));
  const btnAdd = el("button", {}, "🛋️ فرش");
  const btnPaint = el("button", {}, "🎨 الألوان");
  const btnShot = el("button", {}, "📸");
  const btnClose = el("button", { "aria-label": "اقفل" }, "✕");
  root.append(canvas, el("div", { class: "bar" }, el("span", { class: "title" }, title || "المجسم"), btnOrbit, btnWalk, floorSel, btnAdd, btnPaint, btnShot, btnClose), stick, hint, panel);
  document.body.append(root);

  /* three */
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1)); renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.outputColorSpace = THREE.SRGBColorSpace; renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.05;
  const scene = new THREE.Scene(); scene.background = new THREE.Color("#cfe2ee"); scene.fog = new THREE.Fog("#cfe2ee", 60, 160);
  const camera = new THREE.PerspectiveCamera(55, 1, 0.05, 400);
  scene.add(new THREE.HemisphereLight("#ffffff", "#b5a78f", 1.6));
  const roomLight = new THREE.PointLight("#fff3e0", 0, 9, 1.6); scene.add(roomLight);
  const sun = new THREE.DirectionalLight("#fff6e5", 2.2); sun.position.set(-18, 30, 14); sun.castShadow = true; sun.shadow.mapSize.set(1024, 1024);
  const sc = Math.max(layout.W, layout.L) * 0.9 + 6; Object.assign(sun.shadow.camera, { left: -sc, right: sc, top: sc, bottom: -sc, near: 1, far: 100 }); scene.add(sun);
  const cx = layout.W / 2, cz = layout.L / 2; sun.target.position.set(cx, 0, cz); scene.add(sun.target);
  { const order = ["N", "E", "S", "W"], fi0 = order.indexOf(layout.facing || "N"); const vec = { front: [0, 1], right: [1, 0], back: [0, -1], left: [-1, 0] };
    const sideFacingS = ["front", "right", "back", "left"].find((k, i) => order[(fi0 + i) % 4] === "S"), sideFacingW = ["front", "right", "back", "left"].find((k, i) => order[(fi0 + i) % 4] === "W");
    const [sx, sz] = vec[sideFacingS], [wx, wz] = vec[sideFacingW]; sun.position.set(cx + sx * 22 + wx * 8, 30, cz + sz * 22 + wz * 8); }
  // ground + street
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(200, 200), mat("#9bb37f")); ground.rotation.x = -Math.PI / 2; ground.position.set(cx, -0.01, cz); ground.receiveShadow = true; scene.add(ground);
  const walkway = box(layout.W + 4, 0.02, 3, mat("#c9c3b6"), cx, 0.0, layout.L + 1.5); walkway.castShadow = false; scene.add(walkway);
  const street = box(layout.W + 40, 0.02, 7, mat("#4a4f54"), cx, 0.0, layout.L + 6.5); street.castShadow = false; scene.add(street);

  /* building */
  const building = new THREE.Group(); scene.add(building);
  const floorGroups = [], wallBoxes = [], pickables = [], ceilings = [];
  const facadeMat = mat(state.facade), glass = mat("#9fc3d6", { t: 0.35, r: 0.05, m: 0.2 }), frameMat = mat("#3b3b3b", { r: 0.4 }), slabMat = mat("#d9d5ce");
  const roomMats = {}; let entrance = null;
  function roomMat(key, kind) {
    const c = state.colors[key] || {}; const k = key + kind; if (roomMats[k]) return roomMats[k];
    let m;
    if (kind === "floor") { const col = c.floor || "#d8cdb9"; m = new THREE.MeshStandardMaterial({ color: "#ffffff", map: tileTexture(col).clone(), roughness: 0.5 }); m.map.needsUpdate = true; }
    else m = mat(c.wall || "#f4f1ea", { r: 0.95 });
    return (roomMats[k] = m);
  }
  const onEdge = (a, b) => Math.abs(a - b) < 0.05;
  const SIDES = layout.sides || { front: "street", back: "open", right: "open", left: "open" };
  const sideOf = (sd) => (sd.horiz ? (sd.line > 0.05 ? "front" : "back") : (sd.line > 0.05 ? "right" : "left"));
  const neighbourSide = (sd) => SIDES[sideOf(sd)] === "neighbor";
  { const rd = mat("#4a4f54"), pv = mat("#c9c3b6"), add = (w, d, x, z) => { const m = box(w, 0.02, d, rd, x, 0, z); m.castShadow = false; scene.add(m); };
    if (SIDES.right === "street") { add(7, layout.L + 40, layout.W + 6.5, cz); const k = box(3, 0.02, layout.L + 4, pv, layout.W + 1.5, 0, cz); k.castShadow = false; scene.add(k); }
    if (SIDES.left === "street") { add(7, layout.L + 40, -6.5, cz); const k = box(3, 0.02, layout.L + 4, pv, -1.5, 0, cz); k.castShadow = false; scene.add(k); }
    if (SIDES.back === "street") add(layout.W + 40, 7, cx, -6.5);
    // neighbours: simple grey masses so blank walls make sense
    const nb = mat("#d6d2ca", { t: 0.35 }), mass = (w, d, x, z) => scene.add(box(w, layout.floors.length * H + 1.5, d, nb, x, (layout.floors.length * H + 1.5) / 2, z));
    if (SIDES.right === "neighbor") mass(8, layout.L, layout.W + 4.3, cz); if (SIDES.left === "neighbor") mass(8, layout.L, -4.3, cz); if (SIDES.back === "neighbor") mass(layout.W, 8, cx, -4.3); }
  // light wells (منور): open to the sky, no floor finish
  const isWell = (n) => /منور|مسقط نور|بير سلم نور/.test(n);
  // spanning tree of doors: from the biggest living room
  function doorsFor(rooms) {
    const doors = [];
    const living = rooms.map((r, i) => i).filter((i) => /ريسبشن|صال|معيش|استقبال|ليفنج/.test(rooms[i].name));
    const front = (i) => Math.abs(rooms[i].y + rooms[i].h - layout.L) < 0.05;
    const biggest = (ids) => ids.reduce((bi, i) => (rooms[i].w * rooms[i].h > rooms[bi].w * rooms[bi].h ? i : bi), ids[0]);
    const hub = living.length ? biggest(living.filter(front).length ? living.filter(front) : living) : biggest(rooms.map((r, i) => i));
    const seen = new Set([hub]), q = [hub];
    const shared = (a, b) => {
      const A = rooms[a], B = rooms[b];
      if (onEdge(A.x + A.w, B.x) || onEdge(B.x + B.w, A.x)) { const x = onEdge(A.x + A.w, B.x) ? B.x : A.x, y0 = Math.max(A.y, B.y), y1 = Math.min(A.y + A.h, B.y + B.h); if (y1 - y0 >= DOOR_W + 0.3) return { axis: "x", at: x, c: (y0 + y1) / 2 }; }
      if (onEdge(A.y + A.h, B.y) || onEdge(B.y + B.h, A.y)) { const y = onEdge(A.y + A.h, B.y) ? B.y : A.y, x0 = Math.max(A.x, B.x), x1 = Math.min(A.x + A.w, B.x + B.w); if (x1 - x0 >= DOOR_W + 0.3) return { axis: "y", at: y, c: (x0 + x1) / 2 }; }
      return null;
    };
    while (q.length) { const a = q.shift(); rooms.forEach((_, b) => { if (seen.has(b)) return; const s = shared(a, b); if (s) { seen.add(b); q.push(b); doors.push(s); } }); }
    // rooms not reached: connect to any neighbour
    rooms.forEach((_, b) => { if (seen.has(b)) return; for (let a = 0; a < rooms.length; a++) { if (a === b) continue; const s = shared(a, b); if (s) { doors.push(s); seen.add(b); break; } } });
    return { doors, hub };
  }
  // one wall segment along x (horizontal) or z (vertical) with openings
  function wallSeg(group, horiz, fixed, from, to, y0, openings, m, thick, key) {
    const cuts = openings.filter((o) => o.c - o.w / 2 < to && o.c + o.w / 2 > from).sort((a, b) => a.c - b.c);
    let p = from; const pieces = [];
    const put = (a, b, yb, yt) => { if (b - a < 0.02 || yt - yb < 0.02) return; const len = b - a, mid = (a + b) / 2, hh = yt - yb;
      const g = horiz ? box(len, hh, thick, m, mid, y0 + yb + hh / 2, fixed) : box(thick, hh, len, m, fixed, y0 + yb + hh / 2, mid);
      g.userData = { kind: "wall", key }; group.add(g); pickables.push(g); pieces.push(g); };
    for (const o of cuts) {
      const a = Math.max(from, o.c - o.w / 2), b = Math.min(to, o.c + o.w / 2);
      put(p, a, 0, H - SLAB);
      if (o.bottom > 0) put(a, b, 0, o.bottom);
      put(a, b, o.top, H - SLAB);
      if (o.glass) { const gw = b - a, gh = o.top - o.bottom, mid = (a + b) / 2; const g = horiz ? box(gw, gh, 0.03, glass, mid, y0 + o.bottom + gh / 2, fixed) : box(0.03, gh, gw, glass, fixed, y0 + o.bottom + gh / 2, mid); g.castShadow = false; group.add(g);
        const f1 = horiz ? box(gw, 0.05, thick + 0.02, frameMat, mid, y0 + o.bottom, fixed) : box(thick + 0.02, 0.05, gw, frameMat, fixed, y0 + o.bottom, mid); group.add(f1); }
      else wallBoxes.push(null);
      p = b;
    }
    put(p, to, 0, H - SLAB);
    // collision boxes (2D) for the solid parts below door height
    let q0 = from; for (const o of cuts) { const a = Math.max(from, o.c - o.w / 2); if (!(o.bottom === 0)) { } else { pushCol(horiz, fixed, q0, a, thick, group.userData.fi); q0 = Math.min(to, o.c + o.w / 2); } }
    pushCol(horiz, fixed, q0, to, thick, group.userData.fi);
  }
  function pushCol(horiz, fixed, a, b, t, fi) { if (b - a < 0.02) return; wallBoxes.push(horiz ? { fi, x0: a, x1: b, z0: fixed - t / 2, z1: fixed + t / 2 } : { fi, x0: fixed - t / 2, x1: fixed + t / 2, z0: a, z1: b }); }

  layout.floors.forEach((f, fi) => {
    const g = new THREE.Group(); g.userData.fi = fi; const y0 = fi * H; building.add(g); floorGroups.push(g);
    // slab
    const slab = box(layout.W + 0.3, SLAB, layout.L + 0.3, slabMat, cx, y0 - SLAB / 2 + 0.001, cz); g.add(slab);
    const ceil = new THREE.Mesh(new THREE.PlaneGeometry(layout.W, layout.L), mat("#fbfaf7", { r: 1 })); ceil.rotation.x = Math.PI / 2; ceil.position.set(cx, y0 + H - SLAB - 0.01, cz); ceil.visible = false; ceil.userData.ceiling = true; g.add(ceil); ceilings.push(ceil);
    const { doors, hub } = doorsFor(f.rooms);
    if (fi === 0) {
      const rank = { street: 0, open: 1, neighbor: 9 };
      const R = f.rooms, ext = (r) => [{ line: r.y + r.h, horiz: true, ok: onEdge(r.y + r.h, layout.L) }, { line: r.y, horiz: true, ok: onEdge(r.y, 0) }, { line: r.x, horiz: false, ok: onEdge(r.x, 0) }, { line: r.x + r.w, horiz: false, ok: onEdge(r.x + r.w, layout.W) }]
        .filter((e) => e.ok && SIDES[sideOf(e)] !== "neighbor").sort((a, b) => rank[SIDES[sideOf(a)]] - rank[SIDES[sideOf(b)]] || (sideOf(a) === "front" ? -1 : 1));
      const hx = ext(R[hub]); let ri = hub, e = hx[0];
      if (!e) { ri = R.findIndex((r) => !/سلم|درج|حمام/.test(r.name) && onEdge(r.y + r.h, layout.L)); if (ri < 0) ri = 0; e = ext(R[ri])[0]; }
      if (e) { const r = R[ri]; entrance = { ri, ...e, cx: e.horiz ? r.x + r.w / 2 : e.line, cz: e.horiz ? e.line : r.y + r.h / 2 }; }
    }
    f.rooms.forEach((r, ri) => {
      const key = fi + ":" + ri;
      // floor finish
      if (isWell(r.name)) { const gr = box(r.w - 0.05, 0.02, r.h - 0.05, mat("#9a9488"), r.x + r.w / 2, y0 + 0.01, r.y + r.h / 2); gr.castShadow = false; g.add(gr); }
      const fm = roomMat(key, "floor"); const fl = new THREE.Mesh(new THREE.PlaneGeometry(r.w - 0.02, r.h - 0.02), fm); fl.rotation.x = -Math.PI / 2; fl.position.set(r.x + r.w / 2, y0 + 0.012, r.y + r.h / 2); fl.receiveShadow = true;
      fm.map.repeat.set(r.w / 0.6, r.h / 0.6); fl.userData = { kind: "floor", key }; g.add(fl); pickables.push(fl);
      if (/سلم|درج/.test(r.name)) { const sm = mat("#d9d0c1"); const steps = 18, run = Math.max(r.h, r.w) * 0.85 / steps, along = r.h >= r.w; for (let s = 0; s < steps; s++) { const st = along ? box(Math.min(1.1, r.w * 0.9), (s + 1) * (H / steps), run, sm, r.x + r.w / 2, y0 + (s + 1) * (H / steps) / 2, r.y + 0.2 + s * run + run / 2) : box(run, (s + 1) * (H / steps), Math.min(1.1, r.h * 0.9), sm, r.x + 0.2 + s * run + run / 2, y0 + (s + 1) * (H / steps) / 2, r.y + r.h / 2); g.add(st); } }
      const wm = roomMat(key, "wall");
      const sides = [
        { horiz: true, fixed: r.y + WI / 2, from: r.x, to: r.x + r.w, ext: onEdge(r.y, 0), line: r.y, axis: "y" },
        { horiz: true, fixed: r.y + r.h - WI / 2, from: r.x, to: r.x + r.w, ext: onEdge(r.y + r.h, layout.L), line: r.y + r.h, axis: "y" },
        { horiz: false, fixed: r.x + WI / 2, from: r.y, to: r.y + r.h, ext: onEdge(r.x, 0), line: r.x, axis: "x" },
        { horiz: false, fixed: r.x + r.w - WI / 2, from: r.y, to: r.y + r.h, ext: onEdge(r.x + r.w, layout.W), line: r.x + r.w, axis: "x" }];
      for (const sd of sides) {
        const ops = doors.filter((d) => d.axis === sd.axis && Math.abs(d.at - sd.line) < 0.05).map((d) => ({ c: d.c, w: DOOR_W, bottom: 0, top: DOOR_H }));
        if (sd.ext) {
          const len = sd.to - sd.from;
          if (fi === 0 && entrance && entrance.ri === ri && entrance.line === sd.line && entrance.horiz === sd.horiz) ops.push({ c: (sd.from + sd.to) / 2, w: 1.2, bottom: 0, top: 2.3 });          // main entrance
          else if (!/سلم|درج/.test(r.name) && len > 1.4 && !neighbourSide(sd)) ops.push({ c: (sd.from + sd.to) / 2, w: Math.min(/حمام/.test(r.name) ? 0.6 : 1.6, len * 0.45), bottom: /حمام/.test(r.name) ? 1.6 : 0.95, top: 2.25, glass: true });
          // facade skin outside
          const t = 0.06, off = sd.horiz ? (sd.line === 0 ? -t / 2 : t / 2) : (sd.line === 0 ? -t / 2 : t / 2);
          wallSeg(g, sd.horiz, sd.line + off, sd.from, sd.to, y0, ops, facadeMat, t, "facade");
        }
        wallSeg(g, sd.horiz, sd.fixed, sd.from, sd.to, y0, ops, wm, WI, key);
      }
    });
  });
  // roof slab + parapet
  const topY = layout.floors.length * H; const roof = new THREE.Group(); building.add(roof);
  roof.add(box(layout.W + 0.3, SLAB, layout.L + 0.3, slabMat, cx, topY - SLAB / 2, cz));
  for (const [w, d, x, z] of [[layout.W + 0.3, 0.15, cx, -0.075], [layout.W + 0.3, 0.15, cx, layout.L + 0.075], [0.15, layout.L + 0.3, -0.075, cz], [0.15, layout.L + 0.3, layout.W + 0.075, cz]]) roof.add(box(w, 1.0, d, facadeMat, x, topY + 0.5, z));

  /* furniture */
  const furnGroup = new THREE.Group(); scene.add(furnGroup); const furnObjs = new Map();
  function makeFurniture(it) {
    const c = CATALOG[it.type]; if (!c) return null;
    const m = mat(it.color || c.color, { r: 0.75 }); const g = new THREE.Group(); c.make(m).forEach((p) => { p.userData = { kind: "furn", id: it.id }; g.add(p); });
    g.position.set(it.x, it.floor * H + 0.012, it.z); g.rotation.y = it.rot || 0; g.userData = { kind: "furn", id: it.id, floor: it.floor }; g.traverse((o) => { if (o.isMesh) { o.castShadow = true; o.userData = { kind: "furn", id: it.id }; pickables.push(o); } });
    furnGroup.add(g); furnObjs.set(it.id, g); return g;
  }
  state.furniture.forEach(makeFurniture);
  function removeFurnitureObj(id) { const g = furnObjs.get(id); if (!g) return; g.traverse((o) => { const i = pickables.indexOf(o); if (i >= 0) pickables.splice(i, 1); }); furnGroup.remove(g); furnObjs.delete(id); }

  /* visibility per floor (dollhouse cut) */
  let viewFloor = "all";
  function applyVisibility() {
    const lim = viewFloor === "all" ? 99 : +viewFloor;
    floorGroups.forEach((g, i) => (g.visible = i <= lim)); roof.visible = viewFloor === "all";
    furnGroup.children.forEach((g) => (g.visible = g.userData.floor <= lim));
    ceilings.forEach((c, i) => (c.visible = mode === "walk" && i === lim));
  }

  /* camera controllers */
  let mode = "orbit"; const orbit = { theta: -0.7, phi: 1.0, r: Math.max(layout.W, layout.L) * 2.2 + 8, tx: cx, ty: layout.floors.length * H * 0.4, tz: cz };
  const walk = { x: cx, z: layout.L - 1.2, yaw: Math.PI, pitch: -0.05, floor: 0, vx: 0, vz: 0 };
  function placeCamera() {
    if (mode === "orbit") {
      orbit.phi = Math.max(0.15, Math.min(1.5, orbit.phi)); orbit.r = Math.max(4, Math.min(120, orbit.r));
      camera.position.set(orbit.tx + orbit.r * Math.sin(orbit.phi) * Math.sin(orbit.theta), orbit.ty + orbit.r * Math.cos(orbit.phi), orbit.tz + orbit.r * Math.sin(orbit.phi) * Math.cos(orbit.theta));
      camera.lookAt(orbit.tx, orbit.ty, orbit.tz);
    } else {
      walk.pitch = Math.max(-1.2, Math.min(1.2, walk.pitch));
      camera.position.set(walk.x, walk.floor * H + EYE, walk.z); roomLight.position.set(walk.x, walk.floor * H + H - 0.5, walk.z);
      camera.lookAt(walk.x - Math.sin(walk.yaw) * Math.cos(walk.pitch), walk.floor * H + EYE + Math.sin(walk.pitch), walk.z - Math.cos(walk.yaw) * Math.cos(walk.pitch));
    }
  }
  function collide(nx, nz) {
    const R = 0.22;
    for (const b of wallBoxes) { if (!b || b.fi !== walk.floor) continue; if (nx + R > b.x0 && nx - R < b.x1 && nz + R > b.z0 && nz - R < b.z1) return true; }
    return false;
  }
  function moveWalk(dx, dz) {
    const nx = walk.x + dx, nz = walk.z + dz;
    if (!collide(nx, walk.z)) walk.x = nx; if (!collide(walk.x, nz)) walk.z = nz;
    walk.x = Math.max(-6, Math.min(layout.W + 6, walk.x)); walk.z = Math.max(-6, Math.min(layout.L + 8, walk.z));
    // stairs: standing in a stair room moves you up/down a floor via the floor buttons; auto-detect floor by selector
  }
  function setMode(m) {
    mode = m; btnOrbit.classList.toggle("on", m === "orbit"); btnWalk.classList.toggle("on", m === "walk"); root.classList.toggle("walk", m === "walk");
    if (m === "walk") {
      walk.floor = viewFloor === "all" ? 0 : +viewFloor; if (viewFloor === "all") { viewFloor = String(walk.floor); floorSel.value = viewFloor; }
      if (walk.floor === 0) { const e = entrance || { cx, cz: layout.L, horiz: true, line: layout.L }; const out = e.horiz ? (e.line > 0.05 ? 1 : -1) : (e.line > 0.05 ? 1 : -1);
        if (e.horiz) { walk.x = e.cx; walk.z = e.cz + out * 2.2; walk.yaw = out > 0 ? 0 : Math.PI; } else { walk.x = e.cx + out * 2.2; walk.z = e.cz; walk.yaw = out > 0 ? Math.PI / 2 : -Math.PI / 2; } } else { const f = layout.floors[walk.floor], r = f.rooms.find((x) => /صال|ريسبشن|معيش/.test(x.name)) || f.rooms[0]; walk.x = r.x + r.w / 2; walk.z = r.y + r.h / 2; }
      hint.textContent = "الدايرة للمشي • اسحب عشان تبص";
    } else hint.textContent = "اسحب عشان تلف • قرّب بصباعين • دوس على أي حاجة عشان تختارها";
    roomLight.intensity = m === "walk" ? 6 : 0; applyVisibility(); placeCamera();
  }
  btnOrbit.onclick = () => setMode("orbit"); btnWalk.onclick = () => setMode("walk");
  floorSel.onchange = () => { viewFloor = floorSel.value; if (mode === "walk") { if (viewFloor === "all") { viewFloor = "0"; floorSel.value = "0"; } walk.floor = +viewFloor; const f = layout.floors[walk.floor], r = f.rooms.find((x) => /صال|ريسبشن|معيش/.test(x.name)) || f.rooms[0]; if (walk.floor > 0) { walk.x = r.x + r.w / 2; walk.z = r.y + r.h / 2; } }
    else { orbit.ty = viewFloor === "all" ? layout.floors.length * H * 0.4 : +viewFloor * H + 1; if (viewFloor !== "all") orbit.phi = Math.min(orbit.phi, 0.9); } applyVisibility(); placeCamera(); };

  /* selection + drag */
  const ray = new THREE.Raycaster(), ndc = new THREE.Vector2(); let selected = null, selectedSurface = null;
  function pick(clientX, clientY) {
    const r = canvas.getBoundingClientRect(); ndc.set(((clientX - r.left) / r.width) * 2 - 1, -((clientY - r.top) / r.height) * 2 + 1); ray.setFromCamera(ndc, camera);
    const hits = ray.intersectObjects(pickables.filter((o) => { let p = o; while (p) { if (!p.visible) return false; p = p.parent; } return true; }), false);
    return hits[0] || null;
  }
  function highlight(id, on) { const g = furnObjs.get(id); if (!g) return; g.traverse((o) => { if (o.isMesh && o.material.emissive) o.material.emissive.set(on ? "#335" : "#000"); }); }
  function selectFurniture(id) {
    if (selected) highlight(selected, false); selected = id; selectedSurface = null; if (!id) { hidePanel(); return; } highlight(id, true);
    const it = state.furniture.find((x) => x.id === id); const c = CATALOG[it.type];
    showPanel(el("div", {}, el("b", {}, c.ar), el("div", { style: "font-size:12px;opacity:.8;margin:4px 0" }, mode === "orbit" ? "اسحبها بصباعك عشان تحركها" : "ارجع لـ 🔄 حوالين عشان تسحبها"),
      el("div", { class: "grid" }, el("button", { onclick: () => { it.rot = (it.rot || 0) + Math.PI / 2; furnObjs.get(id).rotation.y = it.rot; save(); } }, "↻ لف"),
        el("button", { onclick: () => { const n = { ...it, id: Math.random().toString(36).slice(2, 9), x: it.x + 0.5, z: it.z + 0.5 }; state.furniture.push(n); makeFurniture(n); applyVisibility(); selectFurniture(n.id); save(); } }, "⧉ كرر"),
        el("button", { onclick: () => { state.furniture = state.furniture.filter((x) => x.id !== id); removeFurnitureObj(id); selected = null; hidePanel(); save(); } }, "🗑️ شيل"),
        el("button", { onclick: () => selectFurniture(null) }, "تمام")),
      el("div", { style: "margin-top:6px" }, PALETTE.map((col) => el("button", { class: "sw", style: `background:${col}`, "aria-label": "لون", onclick: () => { it.color = col; removeFurnitureObj(id); makeFurniture(it); applyVisibility(); highlight(id, true); save(); } })))));
  }
  function selectSurface(hit) {
    if (selected) highlight(selected, false); selected = null; const { kind, key } = hit.object.userData; selectedSurface = { kind, key };
    const isFacade = key === "facade", isFloor = kind === "floor"; const [fi, ri] = isFacade ? [] : key.split(":").map(Number);
    const room = isFacade ? null : layout.floors[fi].rooms[ri];
    const label = isFacade ? "الواجهة" : `${isFloor ? "أرضية" : "حيطان"} ${room.name} (${layout.floors[fi].name})`;
    const apply = (col) => {
      if (isFacade) { state.facade = col; facadeMat.color.set(col); }
      else { state.colors[key] = state.colors[key] || {}; if (isFloor) { state.colors[key].floor = col; const m = roomMats[key + "floor"]; const rep = m.map.repeat.clone(); m.map = tileTexture(col).clone(); m.map.repeat.copy(rep); m.map.needsUpdate = true; m.needsUpdate = true; } else { state.colors[key].wall = col; roomMats[key + "wall"].color.set(col); } }
      save();
    };
    const cols = isFloor ? FLOORS : PALETTE;
    const custom = el("input", { type: "color", value: "#cccccc", style: "width:40px;height:32px;border:0;background:none", "aria-label": "لون تاني" }); custom.oninput = () => apply(custom.value);
    showPanel(el("div", {}, el("b", {}, "🎨 " + label), el("div", { style: "margin-top:6px" }, cols.map((col) => el("button", { class: "sw", style: `background:${col}`, "aria-label": col, onclick: () => apply(col) })), custom),
      !isFacade && !isFloor ? el("button", { style: "margin-top:6px", onclick: () => { const col = (state.colors[key] || {}).wall; if (!col) return; layout.floors.forEach((f, a) => f.rooms.forEach((_, b) => { const k = a + ":" + b; state.colors[k] = state.colors[k] || {}; state.colors[k].wall = col; if (roomMats[k + "wall"]) roomMats[k + "wall"].color.set(col); })); save(); } }, "طبّق اللون ده على كل الحيطان") : null,
      el("button", { style: "margin-top:6px;margin-inline-start:6px", onclick: hidePanel }, "تمام")));
  }
  function showPanel(c) { panel.replaceChildren(c); panel.classList.add("show"); }
  function hidePanel() { panel.classList.remove("show"); selectedSurface = null; }

  btnPaint.onclick = () => showPanel(el("div", {}, el("b", {}, "🎨 الألوان"), el("p", { style: "font-size:13px;margin:6px 0" }, "دوس على أي حيطة أو أرضية أو على الواجهة من برّه، وهيطلعلك ألوانها هنا."),
    el("div", {}, el("span", {}, "الواجهة: "), PALETTE.slice(0, 12).map((col) => el("button", { class: "sw", style: `background:${col}`, onclick: () => { state.facade = col; facadeMat.color.set(col); save(); } }))), el("button", { style: "margin-top:6px", onclick: hidePanel }, "تمام")));
  btnAdd.onclick = () => {
    const fl = viewFloor === "all" ? 0 : +viewFloor;
    showPanel(el("div", {}, el("b", {}, `🛋️ ضيف فرش في ${layout.floors[fl].name}`), el("div", { class: "grid", style: "margin-top:6px" }, Object.entries(CATALOG).map(([type, c]) => el("button", { onclick: () => {
      // drop it in front of the camera, on this floor, inside the building
      const dir = new THREE.Vector3(); camera.getWorldDirection(dir); let x = camera.position.x + dir.x * 3, z = camera.position.z + dir.z * 3;
      if (mode === "orbit") { x = orbit.tx; z = orbit.tz; } x = Math.max(0.6, Math.min(layout.W - 0.6, x)); z = Math.max(0.6, Math.min(layout.L - 0.6, z));
      const it = { id: Math.random().toString(36).slice(2, 9), type, floor: fl, x, z, rot: 0, color: c.color }; state.furniture.push(it); makeFurniture(it); applyVisibility(); selectFurniture(it.id); save();
    } }, c.ar))), el("button", { style: "margin-top:6px", onclick: hidePanel }, "تمام")));
  };
  btnShot.onclick = () => { renderer.render(scene, camera); const a = el("a", { href: canvas.toDataURL("image/jpeg", 0.9), download: "نسيم-3D.jpg" }); document.body.append(a); a.click(); a.remove(); };
  btnClose.onclick = () => { save(); cancelAnimationFrame(raf); window.removeEventListener("resize", resize); window.removeEventListener("keydown", kd); window.removeEventListener("keyup", ku); renderer.dispose(); root.remove(); };

  /* pointer input: orbit / pan / pinch / drag furniture / walk look + joystick */
  const pts = new Map(); let gesture = null, dragId = null, downAt = null, moved = 0, joy = null; const floorPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0), tmp = new THREE.Vector3();
  canvas.addEventListener("pointerdown", (e) => {
    canvas.setPointerCapture(e.pointerId); pts.set(e.pointerId, { x: e.clientX, y: e.clientY }); downAt = { x: e.clientX, y: e.clientY, t: Date.now() }; moved = 0;
    if (mode === "walk") { const sr = stick.getBoundingClientRect(); if (e.clientX < sr.right + 30 && e.clientY > sr.top - 30) { joy = { id: e.pointerId, cx: sr.left + sr.width / 2, cy: sr.top + sr.height / 2, dx: 0, dy: 0 }; return; } }
    if (mode === "orbit" && pts.size === 1 && selected) { const h = pick(e.clientX, e.clientY); if (h && h.object.userData.kind === "furn" && h.object.userData.id === selected) { dragId = selected; return; } }
  });
  canvas.addEventListener("pointermove", (e) => {
    const p = pts.get(e.pointerId); if (!p) return; const dx = e.clientX - p.x, dy = e.clientY - p.y; p.x = e.clientX; p.y = e.clientY; moved += Math.abs(dx) + Math.abs(dy);
    if (joy && joy.id === e.pointerId) { joy.dx = Math.max(-50, Math.min(50, e.clientX - joy.cx)); joy.dy = Math.max(-50, Math.min(50, e.clientY - joy.cy)); stick.firstChild.style.transform = `translate(${joy.dx}px,${joy.dy}px)`; return; }
    if (dragId) {
      const it = state.furniture.find((x) => x.id === dragId); const r = canvas.getBoundingClientRect(); ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1); ray.setFromCamera(ndc, camera);
      floorPlane.constant = -(it.floor * H); if (ray.ray.intersectPlane(floorPlane, tmp)) { it.x = Math.max(0.2, Math.min(layout.W - 0.2, tmp.x)); it.z = Math.max(0.2, Math.min(layout.L - 0.2, tmp.z)); furnObjs.get(dragId).position.set(it.x, it.floor * H + 0.012, it.z); }
      return;
    }
    if (mode === "walk") { walk.yaw -= dx * 0.005; walk.pitch -= dy * 0.004; return; }
    if (pts.size === 2) {
      const [a, b] = [...pts.values()]; const d = Math.hypot(a.x - b.x, a.y - b.y); if (gesture && gesture.d) { orbit.r *= gesture.d / d; const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2; pan(mx - gesture.mx, my - gesture.my); }
      gesture = { d, mx: (a.x + b.x) / 2, my: (a.y + b.y) / 2 }; return;
    }
    if (e.buttons === 2 || e.shiftKey) { pan(dx, dy); return; }
    orbit.theta -= dx * 0.006; orbit.phi -= dy * 0.005;
  });
  function pan(dx, dy) { const s = orbit.r * 0.0015; const fx = Math.cos(orbit.theta), fz = -Math.sin(orbit.theta); orbit.tx -= dx * s * fx; orbit.tz -= dx * s * fz; orbit.ty += dy * s; orbit.ty = Math.max(0, Math.min(layout.floors.length * H + 3, orbit.ty)); }
  const up = (e) => {
    pts.delete(e.pointerId); if (pts.size < 2) gesture = null;
    if (joy && joy.id === e.pointerId) { joy = null; stick.firstChild.style.transform = ""; return; }
    if (dragId) { dragId = null; save(); return; }
    if (downAt && moved < 8 && Date.now() - downAt.t < 500) {   // a tap
      const h = pick(e.clientX, e.clientY);
      if (!h) { selectFurniture(null); hidePanel(); }
      else if (h.object.userData.kind === "furn") selectFurniture(h.object.userData.id);
      else if (h.object.userData.kind === "wall" || h.object.userData.kind === "floor") { selectSurface(h); if (mode === "orbit" && h.object.userData.kind === "floor") { orbit.tx = h.point.x; orbit.tz = h.point.z; } }
    }
    downAt = null;
  };
  canvas.addEventListener("pointerup", up); canvas.addEventListener("pointercancel", up); canvas.addEventListener("contextmenu", (e) => e.preventDefault());
  canvas.addEventListener("wheel", (e) => { e.preventDefault(); if (mode === "orbit") orbit.r *= e.deltaY > 0 ? 1.1 : 0.9; else { const s = e.deltaY > 0 ? -0.4 : 0.4; moveWalk(-Math.sin(walk.yaw) * s, -Math.cos(walk.yaw) * s); } }, { passive: false });
  const keys = new Set(); const kd = (e) => { keys.add(e.key.toLowerCase()); if (e.key === "Escape") btnClose.click(); }; const ku = (e) => keys.delete(e.key.toLowerCase());
  window.addEventListener("keydown", kd); window.addEventListener("keyup", ku);

  function resize() { const w = root.clientWidth, h = root.clientHeight; renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix(); }
  window.addEventListener("resize", resize); resize();
  let raf = 0, last = performance.now();
  function frame(t) {
    const dt = Math.min(0.05, (t - last) / 1000); last = t;
    if (mode === "walk") {
      let f = 0, s = 0; if (keys.has("w") || keys.has("arrowup")) f += 1; if (keys.has("s") || keys.has("arrowdown")) f -= 1; if (keys.has("a")) s -= 1; if (keys.has("d")) s += 1;
      if (keys.has("arrowleft")) walk.yaw += dt * 1.8; if (keys.has("arrowright")) walk.yaw -= dt * 1.8;
      if (joy) { f -= joy.dy / 50; s += joy.dx / 50; }
      if (f || s) { const sp = 1.6 * dt; moveWalk((-Math.sin(walk.yaw) * f + Math.cos(walk.yaw) * s) * sp, (-Math.cos(walk.yaw) * f - Math.sin(walk.yaw) * s) * sp); }
    }
    placeCamera(); renderer.render(scene, camera); raf = requestAnimationFrame(frame);
  }
  applyVisibility(); setMode("orbit"); raf = requestAnimationFrame(frame);
  if (firstTime) save();
  const ctl = {
    renderer, camera, scene, orbit, walk, setMode, canvas,
    setFloor: (v) => { viewFloor = String(v); applyVisibility(); },
    entrance: () => entrance, hubCenter: () => { const f = layout.floors[0], r = f.rooms.find((x) => /صال|ريسبشن|معيش|استقبال/.test(x.name)) || f.rooms[0]; return { x: r.x + r.w / 2, z: r.y + r.h / 2 }; },
    render: () => { placeCamera(); renderer.render(scene, camera); },
  };
  return { close: () => btnClose.click(), state, ctl, _debug: { scene, camera, setMode, pick, selectFurniture, orbit, walk, floorSel, renderer } };
}


/* ---------- media for the structural sheet: facade pictures and a fly-around + walk-in video, made on the phone ---------- */
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
export async function shots(opts) {
  const st = open({ ...opts, headless: true, onSave: null }), c = st.ctl, out = [];
  const Lo = opts.layout, hgt = Lo.floors.length * H, fit = Math.max(Lo.W * 0.95, hgt * 1.7, 8);
  const reach = Math.hypot(Lo.W, Lo.L) / 2;   // camera distance is measured from the building centre
  const sd0 = Lo.sides || {}, corner = sd0.right === "street" || (sd0.right !== "neighbor" && sd0.left === "neighbor") ? 0.7 : -0.7;
  const views = [{ theta: 0, phi: 1.38, r: Lo.L / 2 + fit * 1.15 + 4 }, { theta: corner, phi: 1.2, r: reach + fit * 1.2 + 4 }, { theta: -corner * 0.9, phi: 0.8, r: reach + fit * 1.35 + 5 }];
  await wait(150);
  for (const v of views) { Object.assign(c.orbit, v, { ty: hgt * 0.5 }); c.render(); await wait(60); c.render(); out.push(c.canvas.toDataURL("image/jpeg", 0.9)); }
  st.close(); return out;
}
export async function recordTour(opts) {
  const st = open({ ...opts, headless: true, onSave: null }), c = st.ctl, L = opts.layout;
  const stream = c.canvas.captureStream(30);
  const type = ["video/mp4;codecs=avc1", "video/mp4", "video/webm;codecs=vp9", "video/webm"].find((t) => window.MediaRecorder && MediaRecorder.isTypeSupported(t)) || "video/webm";
  const rec = new MediaRecorder(stream, { mimeType: type, videoBitsPerSecond: 4_000_000 }), chunks = [];
  rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
  const done = new Promise((r) => (rec.onstop = r));
  const big = Math.max(L.W, L.L, L.floors.length * H);
  const anim = (ms, step) => new Promise((res) => { const t0 = performance.now(); const f = (t) => { const k = Math.min(1, (t - t0) / ms); step(k * k * (3 - 2 * k)); c.render(); if (k < 1) requestAnimationFrame(f); else res(); }; requestAnimationFrame(f); });
  rec.start(250);
  const say = (t) => opts.onProgress && opts.onProgress(t);
  say("🎬 لفة حوالين المبنى…");
  c.setMode("orbit"); c.setFloor("all");
  await anim(10000, (k) => Object.assign(c.orbit, { theta: -1.3 + k * 2.6, phi: 1.25 - 0.15 * Math.sin(k * Math.PI), r: Math.max(L.W, L.L) * 1.3 + L.floors.length * H + 3, ty: L.floors.length * H * 0.45 }));
  say("🎬 الدور الأرضي من فوق…");
  c.setFloor(0);
  await anim(4000, (k) => Object.assign(c.orbit, { theta: 1.3 - k * 1.3, phi: 1.0 - k * 0.45, r: Math.max(L.W, L.L) * 1.35 + 3, ty: 1 }));
  say("🎬 جولة جوه…");
  c.setMode("walk"); c.setFloor(0);
  const e = c.entrance() || { cx: L.W / 2, cz: L.L, horiz: true, line: L.L }, hub = c.hubCenter();
  const sx = c.walk.x, sz = c.walk.z, yaw0 = c.walk.yaw;
  await anim(5000, (k) => { c.walk.x = sx + (hub.x - sx) * k; c.walk.z = sz + (hub.z - sz) * k; c.walk.yaw = yaw0; c.walk.pitch = -0.05; });
  await anim(7000, (k) => { c.walk.yaw = yaw0 + k * Math.PI * 2; });
  rec.stop(); await done; st.close();
  return new Blob(chunks, { type: type.split(";")[0] });
}
