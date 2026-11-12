// 제주 원도심 길안내 POC. 비용·통행 제약은 build_demo_graph.py(우리 엔진)가 구워 둠 → 여기선 탐색·표시만.
const G = window.GRAPH;
const $ = (id) => document.getElementById(id);
const TYPES = [
  { k: "safe", ko: "안전환경 우선", en: "Safer environment", c: "var(--c-safe)", hex: "#14a06f", col: 4 },
  { k: "short", ko: "최단", en: "Shortest", c: "var(--c-short)", hex: "#2f6fdf", col: 2 },
  { k: "bal", ko: "균형", en: "Balanced", c: "var(--c-bal)", hex: "#f28c28", col: 3 },
];
const S = { lang: "ko", from: null, to: null, wc: false, routes: null, sel: null, fac: new Set(), gap: false };
const T = (ko, en) => (S.lang === "en" ? en : ko);
const norm = (s) => String(s || "").replace(/\s+/g, "");

// ---------- 그래프 ----------
const out = [];
if (G) G.edges.forEach((e, i) => (out[e[0]] ||= []).push(i));
const bbox = G
  ? G.nodes.reduce((b, [x, y]) => [Math.min(b[0], x), Math.min(b[1], y), Math.max(b[2], x), Math.max(b[3], y)], [180, 90, -180, -90])
  : [126.514, 33.507, 126.534, 33.519];
const metres = (a, b) => {
  const kx = 111320 * Math.cos((a[1] * Math.PI) / 180);
  return Math.hypot((a[0] - b[0]) * kx, (a[1] - b[1]) * 110540);
};
function nearestNode(pt, mask = 1) {
  let best = -1, bd = Infinity;
  G.nodes.forEach((n, i) => {
    if (!out[i]?.some((ei) => G.edges[ei][5] & mask)) return;
    const d = metres(pt, n);
    if (d < bd) (bd = d), (best = i);
  });
  return { node: best, dist: bd };
}

// 다익스트라 (sources: Map node→시작값). ponytail: 단순 이진 힙, 그래프 수천 노드면 충분.
function dijkstra(sources, col, mask, target = -1) {
  const dist = new Map(sources), prev = new Map(), heap = [...sources].map(([n, d]) => [d, n]);
  const push = (x) => { heap.push(x); let i = heap.length - 1; while (i) { const p = (i - 1) >> 1; if (heap[p][0] <= heap[i][0]) break; [heap[p], heap[i]] = [heap[i], heap[p]]; i = p; } };
  const pop = () => { const top = heap[0], last = heap.pop(); if (heap.length) { heap[0] = last; let i = 0; for (;;) { const l = 2 * i + 1, r = l + 1; let m = i; if (l < heap.length && heap[l][0] < heap[m][0]) m = l; if (r < heap.length && heap[r][0] < heap[m][0]) m = r; if (m === i) break; [heap[m], heap[i]] = [heap[i], heap[m]]; i = m; } } return top; };
  heap.sort((a, b) => a[0] - b[0]);
  while (heap.length) {
    const [d, u] = pop();
    if (d > dist.get(u)) continue;
    if (u === target) break;
    for (const ei of out[u] || []) {
      const e = G.edges[ei];
      if (!(e[5] & mask)) continue;
      const nd = d + e[col];
      if (nd < (dist.get(e[1]) ?? Infinity)) dist.set(e[1], nd), prev.set(e[1], ei), push([nd, e[1]]);
    }
  }
  return { dist, prev };
}

function route(type) {
  const mask = S.wc ? 2 : 1;
  const a = nearestNode([S.from.lon, S.from.lat], mask).node, b = nearestNode([S.to.lon, S.to.lat], mask).node;
  const { dist, prev } = dijkstra(new Map([[a, 0]]), type.col, mask, b);
  if (!dist.has(b)) return null; // F6: 조건을 몰래 풀지 않음
  const edges = [];
  for (let n = b; n !== a; n = G.edges[prev.get(n)][0]) edges.unshift(prev.get(n));
  let len = 0, unk = 0, lit = 0, litW = 0, cc = 0, ccW = 0, litMiss = 0, ccMiss = 0;
  const coords = [];
  for (const ei of edges) {
    const e = G.edges[ei], L = e[2];
    len += L;
    if (e[5] & 4) unk += L;
    if (e[8] == null) litMiss += L; else (lit += e[8] * L), (litW += L);
    if (e[9] == null) ccMiss += L; else (cc += e[9] * L), (ccW += L);
    const g = G.geoms[e[6]];
    coords.push(...(e[7] ? [...g].reverse() : g));
  }
  return { type, edges, coords, nodes: [a, ...edges.map((ei) => G.edges[ei][1])], len,
    unk: len ? unk / len : 0, lit: litW ? lit / litW : null, cc: ccW ? cc / ccW : null,
    litMiss: len ? litMiss / len : 0, ccMiss: len ? ccMiss / len : 0 };
}

// ---------- 지도 (카카오 / 키 없으면 OSM 길 SVG) ----------
function kakaoMap(el) {
  const k = kakao.maps, map = new k.Map(el, { center: new k.LatLng(33.5133, 126.5245), level: 4 });
  const L = {}, ll = ([x, y]) => new k.LatLng(y, x);
  const add = (layer, o) => (o.setMap(map), (L[layer] ||= []).push(o), o);
  return {
    clear: (layer) => (L[layer] || []).splice(0).forEach((o) => o.setMap(null)),
    line(layer, coords, { color, width, opacity = 1, onclick }) {
      const p = add(layer, new k.Polyline({ path: coords.map(ll), strokeColor: color, strokeWeight: width, strokeOpacity: opacity }));
      if (onclick) k.event.addListener(p, "click", onclick);
    },
    marker: (layer, pt, label, kind) => add(layer, new k.CustomOverlay({ position: ll(pt), yAnchor: 1, content: `<div class="mk ${kind}">${label}</div>` })),
    rect: (layer, [w, s, e, n], color) => add(layer, new k.Rectangle({ bounds: new k.LatLngBounds(ll([w, s]), ll([e, n])), strokeWeight: 0, fillColor: color, fillOpacity: 0.35 })),
    fit(coords) {
      const b = new k.LatLngBounds();
      coords.forEach((c) => b.extend(ll(c)));
      map.setBounds(b, 90, 30, el.clientHeight * 0.55, 30);
    },
  };
}

function svgMap(el) {
  const NS = "http://www.w3.org/2000/svg", svg = document.createElementNS(NS, "svg");
  const kx = 111320 * Math.cos((33.513 * Math.PI) / 180), ky = 110540;
  const P = ([x, y]) => [(x - bbox[0]) * kx, (bbox[3] - y) * ky];
  const W = (bbox[2] - bbox[0]) * kx, H = (bbox[3] - bbox[1]) * ky;
  let vb = [0, 0, W, H];
  const setVB = () => svg.setAttribute("viewBox", vb.join(" "));
  const mk = (tag, attrs, parent) => { const n = document.createElementNS(NS, tag); for (const a in attrs) n.setAttribute(a, attrs[a]); parent.appendChild(n); return n; };
  const path = (coords) => "M" + coords.map((c) => P(c).map((v) => v.toFixed(1)).join(",")).join("L");
  mk("rect", { x: -5000, y: -5000, width: W + 10000, height: H + 10000, fill: "#eef2f1" }, svg);
  if (G) mk("path", { d: G.geoms.map(path).join(""), fill: "none", stroke: "#c9d1d3", "stroke-width": 3, "stroke-linecap": "round", "vector-effect": "non-scaling-stroke" }, svg);
  const L = {};
  const layer = (name) => (L[name] ||= mk("g", {}, svg));
  ["gap", "routes"].forEach(layer); // 마커 레이어는 나중에 생겨 위에 그려짐
  setVB();
  el.appendChild(svg);
  // 드래그 이동 + 휠/핀치 확대
  const pts = new Map();
  let last = null;
  const scale = () => vb[2] / svg.clientWidth;
  svg.addEventListener("pointerdown", (e) => { svg.setPointerCapture(e.pointerId); pts.set(e.pointerId, [e.clientX, e.clientY]); });
  svg.addEventListener("pointerup", (e) => { pts.delete(e.pointerId); last = null; });
  svg.addEventListener("pointermove", (e) => {
    if (!pts.has(e.pointerId)) return;
    const prevPt = pts.get(e.pointerId);
    pts.set(e.pointerId, [e.clientX, e.clientY]);
    if (pts.size === 2) {
      const [a, b] = [...pts.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]);
      if (last) zoom(last / d, (a[0] + b[0]) / 2, (a[1] + b[1]) / 2);
      last = d;
      return;
    }
    vb[0] -= (e.clientX - prevPt[0]) * scale(); vb[1] -= (e.clientY - prevPt[1]) * scale(); setVB();
  });
  function zoom(f, cx, cy) {
    const r = svg.getBoundingClientRect(), s = scale();
    const mx = vb[0] + (cx - r.left) * s, my = vb[1] + (cy - r.top) * s;
    vb = [mx - (mx - vb[0]) * f, my - (my - vb[1]) * f, vb[2] * f, vb[3] * f]; setVB();
  }
  svg.addEventListener("wheel", (e) => { e.preventDefault(); zoom(e.deltaY > 0 ? 1.15 : 1 / 1.15, e.clientX, e.clientY); }, { passive: false });
  return {
    clear: (name) => layer(name).replaceChildren(),
    line(name, coords, { color, width, opacity = 1, onclick }) {
      const p = mk("path", { d: path(coords), fill: "none", stroke: color, "stroke-width": width, "stroke-opacity": opacity, "stroke-linecap": "round", "stroke-linejoin": "round", "vector-effect": "non-scaling-stroke" }, layer(name));
      if (onclick) (p.style.cursor = "pointer"), p.addEventListener("click", onclick);
    },
    marker(name, pt, label, kind) {
      const [x, y] = P(pt), g = mk("g", {}, layer(name));
      const fill = { start: "#0b6e79", end: "#f28c28", toilets: "#fff", pharmacy: "#fff", place: "#2b2f33" }[kind];
      mk("circle", { cx: x, cy: y, r: kind === "place" ? 3 : 6, fill, stroke: kind === "place" ? "none" : "#0b6e79", "stroke-width": 2, "vector-effect": "non-scaling-stroke" }, g);
      const t = mk("text", { x, y, dy: -10, "text-anchor": "middle", "font-size": 12 * scale(), "font-weight": 700, fill: "#2b2f33", stroke: "#fff", "stroke-width": 3 * scale(), "paint-order": "stroke" }, g);
      t.textContent = label;
    },
    rect(name, box, color) {
      const [x1, y1] = P([box[0], box[3]]), [x2, y2] = P([box[2], box[1]]);
      mk("rect", { x: x1, y: y1, width: x2 - x1, height: y2 - y1, fill: color, "fill-opacity": 0.35 }, layer(name));
    },
    fit(coords) {
      const p = coords.map(P), xs = p.map((v) => v[0]), ys = p.map((v) => v[1]);
      const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
      // 지도 위쪽 45%에 들어오게 (아래는 시트)
      const ar = svg.clientWidth / svg.clientHeight, w = Math.max(x1 - x0, 300) * 1.3, h = Math.max(y1 - y0, 200) * 1.3 / 0.45;
      const vw = Math.max(w, h * ar), vh = vw / ar;
      vb = [(x0 + x1) / 2 - vw / 2, (y0 + y1) / 2 - vh * 0.3, vw, vh]; setVB();
    },
  };
}
let M;
function initMap(cb) {
  if (window.KAKAO_JS_KEY) {
    const s = document.createElement("script");
    s.src = `https://dapi.kakao.com/v2/maps/sdk.js?appkey=${encodeURIComponent(window.KAKAO_JS_KEY)}&autoload=false`;
    s.onload = () => kakao.maps.load(() => ((M = kakaoMap($("map"))), ($("kakaoAttr").textContent = " · Kakao"), cb()));
    s.onerror = () => ((M = svgMap($("map"))), cb());
    document.head.appendChild(s);
  } else {
    M = svgMap($("map"));
    cb();
  }
}

// ---------- 장소 ----------
const PLACES = window.PLACES.map((p) => {
  const c = G?.named?.[p.name];
  return c ? { ...p, lon: c[0], lat: c[1] } : p;
});
const findPlace = (name) => PLACES.find((p) => norm(p.name) === norm(name)); // F4: 공백 제거 후 비교
const isOutside = (name) => window.OUT_OF_AREA.some((o) => norm(name).includes(o));
const suggest = (n = 4) => [...PLACES].sort(() => Math.random() - 0.5).slice(0, n);

// ---------- 화면 ----------
function say(text, chips = [], who = "ai") {
  const d = document.createElement("div");
  d.className = `msg ${who}`;
  d.textContent = text;
  if (chips.length) {
    const c = document.createElement("div");
    c.className = "chips";
    chips.forEach(([label, fn, cls = ""]) => {
      const b = document.createElement("button");
      b.className = `chip ${cls}`;
      b.textContent = label;
      b.onclick = fn;
      c.appendChild(b);
    });
    d.appendChild(c);
  }
  $("log").appendChild(d);
  $("scroll").scrollTop = $("scroll").scrollHeight;
}

function drawMarkers() {
  M.clear("ends");
  if (S.from) M.marker("ends", [S.from.lon, S.from.lat], S.from.name, "start");
  if (S.to) M.marker("ends", [S.to.lon, S.to.lat], S.to.name, "end");
  $("from").textContent = S.from?.name || "-";
  $("to").textContent = S.to?.name || T("목적지를 말해 주세요", "Where to?");
}

function compute() {
  if (!S.from || !S.to || !G) return;
  S.routes = TYPES.map(route);
  $("hero").hidden = true;
  if (!S.routes.some(Boolean)) {
    S.sel = null;
    renderRoutes();
    return say(T("조건에 맞는 경로가 없어요. 휠체어 조건에서는 통행이 확인된 구간만 쓰기 때문에, 조건을 풀어서 대신 안내하지 않아요.",
      "No route meets these conditions. In wheelchair mode only verified segments are used, and we don't silently relax that."));
  }
  if (!S.routes[TYPES.findIndex((t) => t.k === S.sel)]) S.sel = S.routes.find(Boolean).type.k;
  renderRoutes();
}

function renderRoutes() {
  M.clear("routes");
  const cards = $("cards");
  cards.replaceChildren();
  if (!S.routes) return;
  const shortest = S.routes[1];
  const all = [];
  S.routes.forEach((r, i) => {
    const t = TYPES[i], on = t.k === S.sel;
    const card = document.createElement("div"); // button 안에 details 못 넣음
    card.className = `card${on ? " on" : ""}`;
    card.setAttribute("role", "button");
    card.tabIndex = 0;
    card.style.setProperty("--c", t.c);
    card.dataset.feature = "F5";
    if (!r) {
      card.innerHTML = `<div class="t"><span class="dot" style="background:${t.c}"></span>${T(t.ko, t.en)}</div><div class="tag" style="margin-top:8px">${T("조건에 맞는 경로 없음", "No feasible route")}</div>`;
      cards.appendChild(card);
      return;
    }
    const min = Math.max(1, Math.round(r.len / (S.wc ? 50 : 70)));
    const diff = shortest && i !== 1 ? Math.round(r.len - shortest.len) : 0;
    const tag = {
      safe: T(`조명 ${pct(r.lit)} · CCTV 설치 ${pct(r.cc)} 지표`, `Lighting ${pct(r.lit)} · CCTV density ${pct(r.cc)}`),
      short: T("가장 짧은 길", "Shortest distance"),
      bal: T("거리와 환경 지표의 균형", "Balance of distance and environment"),
    }[t.k];
    card.innerHTML = `<div class="t"><span class="dot" style="background:${t.c}"></span>${T(t.ko, t.en)}${on ? `<span class="rec">${T("선택", "Selected")}</span>` : ""}</div>
      <div class="big">${fmtLen(r.len)}<small>${T("도보", "walk")} ${min}${T("분", " min")}${diff > 0 ? ` · +${diff}m` : ""}</small></div>
      <div class="tag">${tag}</div>
      ${r.unk > 0.001 ? `<div class="warn">${T("통행 미확인 구간", "Unverified segments")} ${pct(r.unk)}</div>` : ""}
      <details data-feature="F8" onclick="event.stopPropagation()"><summary>${T("근거 보기", "Evidence")}</summary><p>
        ${T("길 모양·거리", "Geometry")}: OpenStreetMap (© OpenStreetMap contributors)<br>
        ${T("환경 지표", "Indicators")}: <b>DEMO ${T("가상값", "synthetic")}</b> · ${T("가중치", "weights")} ${G.weights[t.k]}<br>
        ${T("기준일", "As of")}: ${G.built}<br>
        ${T("누락률", "Missing")}: ${T("조명", "lighting")} ${pct(r.litMiss)} · CCTV ${pct(r.ccMiss)}<br>
        ${T("지표는 환경 비교용이며 안전을 보장하지 않습니다.", "Indicators compare environments; they do not guarantee safety.")}</p></details>`;
    card.onclick = () => select(t.k);
    cards.appendChild(card);
    all.push(...r.coords);
  });
  // 선택 안 된 경로 먼저, 선택 경로를 위에
  S.routes.forEach((r, i) => r && TYPES[i].k !== S.sel && M.line("routes", r.coords, { color: TYPES[i].hex, width: 5, opacity: 0.45, onclick: () => select(TYPES[i].k) }));
  const sel = S.routes[TYPES.findIndex((t) => t.k === S.sel)];
  if (sel) {
    M.line("routes", sel.coords, { color: "#ffffff", width: 10 });
    M.line("routes", sel.coords, { color: TYPES.find((t) => t.k === S.sel).hex, width: 7 });
  }
  if (all.length) M.fit(all);
  renderFacilities();
}
const pct = (v) => (v == null ? "-" : `${Math.round(v * 100)}%`);
const fmtLen = (m) => (m >= 1000 ? `${(m / 1000).toFixed(1)}km` : `${Math.round(m)}m`);

function select(k) {
  S.sel = k;
  renderRoutes();
}

// F7: 경로 근처 시설 — 길 따라 왕복 우회 거리
function renderFacilities() {
  M.clear("fac");
  const r = S.routes?.[TYPES.findIndex((t) => t.k === S.sel)];
  if (!r || !S.fac.size) return;
  const mask = S.wc ? 2 : 1;
  const { dist } = dijkstra(new Map(r.nodes.map((n) => [n, 0])), 2, mask);
  const found = G.facilities
    .filter((f) => S.fac.has(f.type))
    .map((f) => ({ ...f, d: 2 * (dist.get(nearestNode([f.lon, f.lat], mask).node) ?? Infinity) }))
    .filter((f) => f.d <= 800)
    .sort((a, b) => a.d - b.d)
    .slice(0, 5);
  found.forEach((f) => M.marker("fac", [f.lon, f.lat], `${f.type === "toilets" ? "🚻" : "💊"} +${Math.round(f.d)}m`, f.type));
  const names = { toilets: T("화장실", "toilets"), pharmacy: T("약국", "pharmacies") };
  const kinds = [...S.fac].map((k) => names[k]).join("·");
  say(found.length
    ? T(`가는 길 근처 ${kinds} ${found.length}곳이에요. 가장 가까운 곳은 왕복 ${Math.round(found[0].d)}m 더 걸어요.`, `${found.length} ${kinds} near your route. The closest adds ${Math.round(found[0].d)}m round trip.`)
    : T(`경로 근처 800m 안에 ${kinds}가 없어요.`, `No ${kinds} within 800m detour.`));
}

// F9: 시설 공백 — 화장실·약국까지 길 거리 400m 넘는 구역
function renderGap() {
  M.clear("gap");
  if (!S.gap || !G) return;
  const src = new Map(G.facilities.map((f) => [nearestNode([f.lon, f.lat]).node, 0]));
  const { dist } = dijkstra(src, 2, 1);
  const step = 0.0015; // 약 150m
  let n = 0;
  for (let x = bbox[0]; x < bbox[2]; x += step)
    for (let y = bbox[1]; y < bbox[3]; y += step * 0.84) {
      const c = [x + step / 2, y + step * 0.42], nn = nearestNode(c);
      if (nn.dist > 80) continue; // 길 없는 곳(바다·건물 블록) 제외
      const d = dist.get(nn.node) ?? Infinity;
      if (d > 400) M.rect("gap", [x, y, x + step, y + step * 0.84], d > 700 ? "#d6453d" : "#f2a33a"), n++;
    }
  say(T(`시설 공백 구역 ${n}칸을 표시했어요. 주황은 화장실·약국까지 길로 400m 넘게, 빨강은 700m 넘게 걸리는 곳이에요. (DEMO·OSM 시설 기준)`,
    `Marked ${n} gap cells: orange >400m, red >700m walk to the nearest toilet/pharmacy.`));
}

// ---------- 대화 (F3·F4) ----------
function localParse(text) {
  const n = norm(text);
  const hits = PLACES.map((p) => [n.indexOf(norm(p.name)), p.name]).filter(([i]) => i >= 0).sort((a, b) => a[0] - b[0]).map(([, nm]) => nm);
  const outside = window.OUT_OF_AREA.find((o) => n.includes(o));
  return {
    origin: hits.length > 1 ? hits[0] : null,
    destination: hits.length ? hits[hits.length - 1] : outside || null,
    route_type: /안전|safe/i.test(text) ? "safe" : /최단|빠른|짧은|short|fast/i.test(text) ? "short" : /균형|balanc/i.test(text) ? "bal" : null,
    wheelchair: /휠체어|wheelchair/i.test(text),
    facility: /화장실|toilet|restroom/i.test(text) ? "toilets" : /약국|pharmac/i.test(text) ? "pharmacy" : null,
    lang: /[가-힣]/.test(text) ? "ko" : "en",
    reply: "",
  };
}

async function ask(text) {
  say(text, [], "me");
  let slots = null;
  if (window.HAS_LLM) {
    try {
      const r = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: text }) });
      if (r.ok) slots = await r.json();
    } catch {}
  }
  apply(slots || localParse(text));
}

const placeChips = (list, fn) => list.map((p) => [p.name, () => fn(p), "place"]);

function apply(s) {
  S.lang = s.lang === "en" ? "en" : "ko";
  if (s.wheelchair && !S.wc) setWc(true, false);
  if (s.origin) {
    const o = findPlace(s.origin);
    if (o) S.from = o;
  }
  if (s.destination) {
    const d = findPlace(s.destination);
    if (!d) {
      if (isOutside(s.destination))
        return say(T(`${s.destination}은(는) 현재 안내 지역 밖이에요. 지금은 제주시 원도심 안에서만 길을 안내해요. 이런 곳은 어떠세요?`,
          `${s.destination} is outside the current guide area. I can guide you within Jeju old downtown, for example:`), placeChips(suggest(), goTo));
      return say(T("어느 곳을 말씀하신 건가요? 원도심 장소 중에서 골라 주세요.", "Which place do you mean? Pick one in the old downtown:"), placeChips(suggest(), goTo));
    }
    S.to = d;
  }
  if (s.facility) {
    S.fac.add(s.facility);
    syncFacChips();
  }
  if (!S.to) {
    if (s.facility) return say(T("먼저 목적지를 알려 주세요. 그 길 근처에서 찾아 드릴게요.", "Tell me your destination first, and I'll search along the way."), placeChips(suggest(), goTo));
    return say(s.reply || T("어디로 가실 건가요?", "Where would you like to go?"), placeChips(suggest(), goTo));
  }
  drawMarkers();
  if (s.route_type) S.sel = s.route_type;
  compute();
  if (!S.routes?.some(Boolean)) return;
  const r = S.routes[TYPES.findIndex((t) => t.k === S.sel)];
  const t = TYPES.find((x) => x.k === S.sel);
  if (s.reply) say(s.reply);
  if (!s.route_type)
    return say(T(`${S.from.name}에서 ${S.to.name}까지 길 3개를 찾았어요. 어떤 길로 안내할까요?`, `I found 3 routes from ${S.from.name} to ${S.to.name}. Which one should I guide?`),
      TYPES.map((x) => [T(x.ko, x.en), () => (select(x.k), say(T(`${T(x.ko, x.en)} 경로로 안내할게요.`, `Guiding the ${x.en.toLowerCase()} route.`)))]));
  say(T(`${t.ko} 경로로 ${fmtLen(r.len)}, 걸어서 약 ${Math.round(r.len / (S.wc ? 50 : 70))}분이에요.`, `${t.en} route: ${fmtLen(r.len)}, about ${Math.round(r.len / (S.wc ? 50 : 70))} min on foot.`));
}

function goTo(p) {
  apply({ destination: p.name, lang: S.lang });
}

// ---------- 버튼 ----------
function setWc(on, recompute = true) {
  S.wc = on;
  $("wc").setAttribute("aria-pressed", on);
  if (recompute && S.to) compute();
}
function syncFacChips() {
  $("fToilet").setAttribute("aria-pressed", S.fac.has("toilets"));
  $("fPharm").setAttribute("aria-pressed", S.fac.has("pharmacy"));
}
function toggleFac(type) {
  S.fac.has(type) ? S.fac.delete(type) : S.fac.add(type);
  syncFacChips();
  if (!S.routes) return S.fac.size && say(T("목적지를 정하면 그 길 근처에서 찾아 드릴게요.", "Pick a destination and I'll search along the route."));
  renderFacilities();
}

function locate() {
  if (!navigator.geolocation) return say(T("이 브라우저는 위치를 지원하지 않아요.", "Location not supported."));
  navigator.geolocation.getCurrentPosition(
    ({ coords }) => {
      const pt = [coords.longitude, coords.latitude];
      if (!G || nearestNode(pt).dist > 200)
        return say(T("현재 위치가 안내 지역(제주시 원도심) 밖이에요. 출발지는 동문시장으로 둘게요.", "You're outside the guide area (Jeju old downtown). Keeping Dongmun Market as the start."));
      S.from = { name: T("현재 위치", "My location"), lon: pt[0], lat: pt[1] };
      drawMarkers();
      compute();
    },
    () => say(T("위치 권한이 없어 출발지를 바꾸지 못했어요.", "Couldn't get your location."))
  );
}

initMap(() => {
  S.from = findPlace("동문시장");
  PLACES.forEach((p) => M.marker("places", [p.lon, p.lat], p.name, "place"));
  drawMarkers();
  if (!G) {
    $("notice").hidden = false;
    $("notice").textContent = "길 데이터(poc/data/graph.js)가 아직 없어요. python scripts/fetch_osm.py → python scripts/build_demo_graph.py 를 실행하세요.";
  }
  say(window.HAS_LLM
    ? "안녕하세요! 제주 원도심 어디로 가시나요? 한국어나 영어로 물어보세요."
    : "안녕하세요! 어디로 가시나요? 장소를 고르거나 \"관덕정까지 안전한 길\"처럼 물어보세요. (AI 키가 없어 간단 모드로 동작해요)",
    placeChips(suggest(5), goTo));
  $("ask").onsubmit = (e) => {
    e.preventDefault();
    const v = $("q").value.trim();
    if (v) ask(v), ($("q").value = "");
  };
  $("wc").onclick = () => setWc(!S.wc);
  $("fToilet").onclick = () => toggleFac("toilets");
  $("fPharm").onclick = () => toggleFac("pharmacy");
  $("gap").onclick = () => { S.gap = !S.gap; $("gap").setAttribute("aria-pressed", S.gap); renderGap(); };
  $("locate").onclick = locate;
  $("handle").onclick = () => {
    const small = getComputedStyle(document.documentElement).getPropertyValue("--sheet-h").trim() === "120px";
    document.documentElement.style.setProperty("--sheet-h", small ? "52dvh" : "120px");
  };
});
