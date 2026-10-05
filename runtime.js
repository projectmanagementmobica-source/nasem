/* NASEM standalone runtime.
   Replaces the claude.ai artifact runtime so the same app runs as an installed PWA:
   - db:        memory stored ONLY on this device, in IndexedDB, AES-GCM encrypted with a
                non-extractable key that never leaves the browser.
   - sample:    AI answers from Claude / ChatGPT / Gemini with the user's own keys, primary
                provider + automatic fallback. Text is masked by the app BEFORE it gets here.
   - downloads: normal browser downloads.
   Keys are stored on this device only. Never ship a build with a key inside it. */
(function () {
  "use strict";

  // ---------------------------------------------------------------- config
  const CFG_KEY = "nasem-app-ai";
  // mode "gateway": the app talks only to Nasem's server (keys stay there) -> safe to give to people.
  // mode "keys": your own provider keys on this phone -> personal testing only.
  const DEFAULT_GATEWAY_URL = "";   // set at deploy time, e.g. "https://api.nasem.app"
  const DEFAULTS = {
    mode: DEFAULT_GATEWAY_URL ? "gateway" : "keys",
    primary: "anthropic",
    webSearch: true,
    anthropic: { key: "", main: "claude-sonnet-5", quick: "claude-haiku-4-5-20251001" },
    openai: { key: "", main: "", quick: "", imageModel: "" },
    gemini: { key: "", main: "gemini-3.5-flash", quick: "", imageModel: "" },
    groq: { key: "", main: "openai/gpt-oss-120b", quick: "llama-3.3-70b-versatile" },
  };
  const PROVIDER_NAMES = { anthropic: "Claude", openai: "ChatGPT", gemini: "Gemini", groq: "Groq" };

  function getConfig() {
    let c = {};
    try { c = JSON.parse(localStorage.getItem(CFG_KEY) || "{}"); } catch (e) {}
    return {
      ...DEFAULTS, ...c,
      anthropic: { ...DEFAULTS.anthropic, ...(c.anthropic || {}) },
      openai: { ...DEFAULTS.openai, ...(c.openai || {}) },
      gemini: { ...DEFAULTS.gemini, ...(c.gemini || {}), main: ((c.gemini || {}).main || "").trim() || DEFAULTS.gemini.main },
      groq: { ...DEFAULTS.groq, ...(c.groq || {}), main: ((c.groq || {}).main || "").trim() || DEFAULTS.groq.main },
    };
  }
  function setConfig(c) { localStorage.setItem(CFG_KEY, JSON.stringify(c)); }

  const err = (code, message) => Object.assign(new Error(message || code), { code });

  // ---------------------------------------------------------------- helpers
  async function blobToB64(b) {
    const buf = new Uint8Array(await b.arrayBuffer());
    let s = "";
    for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
    return btoa(s);
  }
  async function downscale(blob) { // ~1.2 MP JPEG, like the hosted runtime did
    const bmp = await createImageBitmap(blob);
    const scale = Math.min(1, Math.sqrt(1.2e6 / (bmp.width * bmp.height)));
    const c = document.createElement("canvas");
    c.width = Math.max(1, Math.round(bmp.width * scale)); c.height = Math.max(1, Math.round(bmp.height * scale));
    c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
    return await new Promise((r) => c.toBlob(r, "image/jpeg", 0.85));
  }
  function parseJSONText(t) {
    const a = t.indexOf("{"), b = t.lastIndexOf("}");
    if (a < 0 || b <= a) throw err("invalid_json", "no JSON object in the answer");
    try { return JSON.parse(t.slice(a, b + 1)); } catch (e) { throw err("invalid_json", e.message); }
  }
  /* Next midnight in Pacific time = when Gemini's daily free quota resets. */
  function nextPacificMidnight() {
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: "America/Los_Angeles", hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" })
      .formatToParts(new Date()).filter((p) => p.type !== "literal").map((p) => [p.type, +p.value]));
    const secs = ((parts.hour % 24) * 3600) + parts.minute * 60 + parts.second;
    return Date.now() + (86400 - secs) * 1000;
  }
  async function httpErr(res) {
    if (res.status === 429) {
      let body = null; try { body = await res.json(); } catch (e) {}
      const details = (body && body.error && body.error.details) || [];
      const quotaIds = details.filter((d) => /QuotaFailure/.test(d["@type"] || "")).flatMap((d) => (d.violations || []).map((v) => v.quotaId || ""));
      const retry = details.find((d) => /RetryInfo/.test(d["@type"] || ""));
      const retryAfter = retry ? parseFloat(String(retry.retryDelay || "").replace("s", "")) || null : (parseFloat(res.headers && res.headers.get && res.headers.get("retry-after")) || null);
      if (quotaIds.some((q) => /PerDay|Daily/i.test(q))) return Object.assign(err("quota_daily", "daily free quota used up"), { resetAt: nextPacificMidnight() });
      return Object.assign(err("rate_limited"), { retryAfter });
    }
    if (res.status === 401 || res.status === 403) return err("bad_key", "key rejected");
    if (res.status === 413) return err("prompt_too_large");
    let msg = ""; try { msg = (await res.text()).slice(0, 300); } catch (e) {}
    return err("upstream_error", `${res.status} ${msg}`);
  }
  const JSON_ONLY = "\n\nReply with ONLY the JSON object, nothing before or after it.";

  // ---------------------------------------------------------------- providers
  async function callAnthropic(cfg, model, prompt, { images, signal, webSearch }) {
    const content = [];
    for (const im of images || []) content.push({ type: "image", source: { type: "base64", media_type: "image/jpeg", data: await blobToB64(await downscale(im)) } });
    content.push({ type: "text", text: prompt + JSON_ONLY });
    const messages = [{ role: "user", content }];
    const body = { model, max_tokens: 4096, messages };
    if (webSearch) body.tools = [{ type: "web_search_20250305", name: "web_search", max_uses: 5 }];
    for (let round = 0; round < 4; round++) {
      const res = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST", signal,
        headers: {
          "content-type": "application/json",
          "x-api-key": cfg.key,
          "anthropic-version": "2023-06-01",
          "anthropic-dangerous-direct-browser-access": "true",
        },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw await httpErr(res);
      const data = await res.json();
      if (data.stop_reason === "pause_turn") { messages.push({ role: "assistant", content: data.content }); continue; }
      const texts = (data.content || []).filter((b) => b.type === "text").map((b) => b.text);
      for (let i = texts.length - 1; i >= 0; i--) if (texts[i].includes("{")) return parseJSONText(texts.slice(i).join("\n"));
      return parseJSONText(texts.join("\n"));
    }
    throw err("invalid_json", "search did not finish");
  }

  async function callOpenAI(cfg, model, prompt, { images, signal }) {
    const content = [{ type: "text", text: prompt + JSON_ONLY }];
    for (const im of images || []) content.push({ type: "image_url", image_url: { url: "data:image/jpeg;base64," + (await blobToB64(await downscale(im))) } });
    const res = await fetch("https://api.openai.com/v1/chat/completions", {
      method: "POST", signal,
      headers: { "content-type": "application/json", authorization: "Bearer " + cfg.key },
      body: JSON.stringify({ model, messages: [{ role: "user", content }], response_format: { type: "json_object" } }),
    });
    if (!res.ok) throw await httpErr(res);
    const d = await res.json();
    return parseJSONText((d.choices && d.choices[0] && d.choices[0].message && d.choices[0].message.content) || "");
  }

  async function callGemini(cfg, model, prompt, { images, signal, webSearch, audio }) {
    const parts = [{ text: prompt + JSON_ONLY }];
    if (audio) parts.push({ inline_data: { mime_type: audio.type || "audio/ogg", data: await blobToB64(audio) } });
    for (const im of images || []) parts.push({ inline_data: { mime_type: "image/jpeg", data: await blobToB64(await downscale(im)) } });
    const body = { contents: [{ role: "user", parts }] };
    if (webSearch) body.tools = [{ google_search: {} }];           // grounding and JSON mode can't be combined
    else body.generationConfig = { responseMimeType: "application/json" };
    const res = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent`, {
      method: "POST", signal,
      headers: { "content-type": "application/json", "x-goog-api-key": cfg.key },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw await httpErr(res);
    const d = await res.json();
    const cand = d.candidates && d.candidates[0];
    return parseJSONText(((cand && cand.content && cand.content.parts) || []).map((p) => p.text || "").join(""));
  }

  // Groq: free tier, no card, OpenAI-compatible. Text only (no images / audio here).
  async function callGroq(cfg, model, prompt, { signal, images }) {
    if (images && images.length) throw err("upstream_error", "groq: images not supported here");
    const res = await fetch("https://api.groq.com/openai/v1/chat/completions", {
      method: "POST", signal,
      headers: { "content-type": "application/json", authorization: "Bearer " + cfg.key },
      body: JSON.stringify({ model, messages: [{ role: "user", content: prompt + JSON_ONLY }], response_format: { type: "json_object" }, temperature: 0.4 }),
    });
    if (!res.ok) throw await httpErr(res);
    const d = await res.json();
    return parseJSONText((d.choices && d.choices[0] && d.choices[0].message && d.choices[0].message.content) || "");
  }

  const CALL = { anthropic: callAnthropic, openai: callOpenAI, gemini: callGemini, groq: callGroq };

  function usable(cfg) {
    const order = [cfg.primary, ...Object.keys(CALL).filter((p) => p !== cfg.primary)];
    return order.filter((p) => cfg[p] && cfg[p].key && (cfg[p].main || cfg[p].quick));
  }

  const state = { lastProvider: null };

  // ---------------------------------------------------------------- gateway (Nasem's server)
  const GW_KEY = "nasem-app-gw";
  function getGW() { let g = {}; try { g = JSON.parse(localStorage.getItem(GW_KEY) || "{}"); } catch (e) {} return { url: DEFAULT_GATEWAY_URL, email: "", access: "", refresh: "", ...g }; }
  function setGW(g) { localStorage.setItem(GW_KEY, JSON.stringify(g)); }
  const base = (u) => String(u || "").trim().replace(/\/+$/, "");

  async function gwRefresh() {
    const g = getGW(); if (!g.refresh) return false;
    try {
      const r = await fetch(base(g.url) + "/v1/auth/refresh", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ refresh_token: g.refresh }) });
      if (!r.ok) { setGW({ url: g.url, email: g.email }); return false; }
      const d = await r.json(); setGW({ ...g, access: d.access_token, refresh: d.refresh_token }); return true;
    } catch (e) { return false; }
  }
  async function gwPost(path, body, signal) {
    const send = () => { const g = getGW(); return fetch(base(g.url) + path, { method: "POST", signal, headers: { "content-type": "application/json", authorization: "Bearer " + g.access }, body: JSON.stringify(body) }); };
    const g = getGW(); if (!g.url || !g.access) throw err("login_required");
    let res = await send();
    if (res.status === 401 && await gwRefresh()) res = await send();   // access token expired -> rotate once
    if (res.status === 401) throw err("login_required");
    return res;
  }
  async function gwLogin(url, email, password) {
    const r = await fetch(base(url) + "/v1/auth/login", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email, password }) });
    if (r.status === 401) throw err("bad_login");
    if (!r.ok) throw err("upstream_error", String(r.status));
    const d = await r.json(); setGW({ url: base(url), email, access: d.access_token, refresh: d.refresh_token });
  }
  async function gwLogout() {
    const g = getGW();
    if (g.refresh) fetch(base(g.url) + "/v1/auth/logout", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ refresh_token: g.refresh }) }).catch(() => {});
    setGW({ url: g.url, email: g.email });
  }
  async function viaGateway(prompt, opts) {
    const images = [];
    for (const im of opts.images ? Array.from(opts.images) : []) images.push({ media_type: "image/jpeg", data_b64: await blobToB64(await downscale(im)) });
    let audio = null;
    if (opts.audio) {
      const t2 = (opts.audio.type || "").toLowerCase();
      const mt = t2.includes("mpeg") || t2.includes("mp3") ? "audio/mpeg" : t2.includes("mp4") || t2.includes("m4a") ? "audio/mp4" : t2.includes("wav") ? "audio/wav" : t2.includes("aac") ? "audio/aac" : t2.includes("webm") ? "audio/webm" : "audio/ogg";
      audio = { media_type: mt, data_b64: await blobToB64(opts.audio) };
    }
    let res;
    try {
      res = await gwPost("/v1/gateway/complete", { prompt, tier: opts.modelTier === "quick" ? "quick" : "main", web_search: !!opts.webSearch, images, ...(audio ? { audio } : {}) }, opts.signal);
    } catch (e) {
      if ((e && e.name === "AbortError") || (opts.signal && opts.signal.aborted)) throw err("cancelled");
      throw e && e.code ? e : err("llm_unavailable", "server unreachable");
    }
    const d = await res.json().catch(() => ({}));
    if (res.ok && d.status === "success") { state.lastProvider = d.provider; return d.data; }
    if (res.status === 429) throw err("rate_limited");
    if (d.error && d.error.code === "llm_output_invalid") throw err("invalid_json", d.error.message);
    throw err("upstream_error", (d.error && d.error.message) || String(res.status));
  }

  // ---------------------------------------------------------------- images (no text: the app draws Arabic itself)
  const NO_TEXT = " No text, letters, words, numbers, logos or watermarks anywhere in the image.";
  const OPENAI_SIZES = { "1:1": "1024x1024", "9:16": "1024x1536", "16:9": "1536x1024" };
  const b64ToBlob = (b64, type) => { const bin = atob(b64), arr = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i); return new Blob([arr], { type }); };
  function canImage() {
    const c = getConfig();
    if (c.mode === "gateway") return !!getGW().access;
    return (!!c.gemini.key && !!c.gemini.imageModel) || (!!c.openai.key && !!c.openai.imageModel);
  }
  async function image(prompt, aspect = "1:1") {
    const c = getConfig();
    if (c.mode === "gateway") {
      const res = await gwPost("/v1/gateway/image", { prompt: prompt.slice(0, 2000), aspect });
      const d = await res.json().catch(() => ({}));
      if (res.ok && d.status === "success") return b64ToBlob(d.image_b64, d.media_type || "image/png");
      throw err(res.status === 429 ? "rate_limited" : "upstream_error", (d.error && d.error.message) || String(res.status));
    }
    let last = null;
    if (c.gemini.key && c.gemini.imageModel) {
      try {
        const res = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(c.gemini.imageModel)}:generateContent`, {
          method: "POST", headers: { "content-type": "application/json", "x-goog-api-key": c.gemini.key },
          body: JSON.stringify({ contents: [{ role: "user", parts: [{ text: `${prompt}. Aspect ratio ${aspect}.${NO_TEXT}` }] }], generationConfig: { responseModalities: ["IMAGE"] } }),
        });
        if (!res.ok) throw await httpErr(res);
        const d = await res.json();
        for (const cand of d.candidates || []) for (const part of (cand.content && cand.content.parts) || []) {
          const inl = part.inline_data || part.inlineData;
          if (inl && inl.data) return b64ToBlob(inl.data, inl.mime_type || inl.mimeType || "image/png");
        }
        throw err("upstream_error", "no image returned");
      } catch (e) { last = e; }
    }
    if (c.openai.key && c.openai.imageModel) {
      const res = await fetch("https://api.openai.com/v1/images/generations", {
        method: "POST", headers: { "content-type": "application/json", authorization: "Bearer " + c.openai.key },
        body: JSON.stringify({ model: c.openai.imageModel, prompt: prompt + NO_TEXT, size: OPENAI_SIZES[aspect] || "1024x1024", n: 1 }),
      });
      if (!res.ok) throw await httpErr(res);
      const d = await res.json(); const b = d.data && d.data[0] && d.data[0].b64_json;
      if (b) return b64ToBlob(b, "image/png");
      throw err("upstream_error", "no image returned");
    }
    throw last || err("no_key");
  }

  // Voice notes: of the three providers only Gemini takes audio directly.
  function canAudio() {
    const c = getConfig();
    if (c.mode === "gateway") return !!getGW().access;
    return !!c.gemini.key && !!(c.gemini.main || c.gemini.quick);
  }

  // Each free Gemini model has its own small daily quota. When one is used up, move to the next.
  const GEMINI_FALLBACKS = ["gemini-3.5-flash", "gemini-2.5-flash", "gemini-2.5-flash-lite"];
  const EXH_KEY = "nasem-exhausted", USE_KEY = "nasem-usage";
  const laDay = () => new Intl.DateTimeFormat("en-CA", { timeZone: "America/Los_Angeles" }).format(new Date());
  function exhausted() { let m = {}; try { m = JSON.parse(localStorage.getItem(EXH_KEY) || "{}"); } catch (e) {} const now = Date.now(); for (const k of Object.keys(m)) if (m[k] < now) delete m[k]; return m; }
  function markExhausted(model, until) { const m = exhausted(); m[model] = until || nextPacificMidnight(); localStorage.setItem(EXH_KEY, JSON.stringify(m)); }
  function countUse(provider, model) {
    let u = {}; try { u = JSON.parse(localStorage.getItem(USE_KEY) || "{}"); } catch (e) {}
    if (u.day !== laDay()) u = { day: laDay(), total: 0, models: {} };
    u.total++; u.models[`${provider}:${model}`] = (u.models[`${provider}:${model}`] || 0) + 1;
    localStorage.setItem(USE_KEY, JSON.stringify(u));
  }
  function usage() { let u = {}; try { u = JSON.parse(localStorage.getItem(USE_KEY) || "{}"); } catch (e) {} return u.day === laDay() ? u : { day: laDay(), total: 0, models: {} }; }
  const sleep = (ms, signal) => new Promise((res, rej) => { const t2 = setTimeout(res, ms); if (signal) signal.addEventListener("abort", () => { clearTimeout(t2); rej(err("cancelled")); }, { once: true }); });

  async function callWithModels(p, pc, models, prompt, o) {
    let last = null; const ex = exhausted(), missing = new Set();
    const list = [...new Set(models.filter(Boolean))];
    const fresh = list.filter((m) => !ex[m]);
    if (!fresh.length) { const soonest = Math.min(...list.map((m) => ex[m] || nextPacificMidnight())); throw Object.assign(err("quota_daily"), { resetAt: soonest }); }
    for (const m of fresh) {
      for (let attempt = 0; attempt < 2; attempt++) {
        try {
          const out = await CALL[p](pc, m, prompt, o);
          countUse(p, m); state.lastModel = m;
          return out;
        } catch (e) {
          if ((e && e.name === "AbortError") || (o.signal && o.signal.aborted)) throw err("cancelled");
          if (e && e.code === "invalid_json") throw e;
          last = e;
          if (e && e.code === "rate_limited" && attempt === 0 && e.retryAfter && e.retryAfter <= 15) { await sleep(e.retryAfter * 1000 + 300, o.signal); continue; }
          if (e && e.code === "quota_daily") { markExhausted(m, e.resetAt); break; }
          if (e && e.code === "upstream_error" && /\b404\b/.test(e.message || "")) { missing.add(m); break; }   // model doesn't exist: try the next one
          if (e && (e.code === "rate_limited" || e.code === "upstream_error")) break;          // try the next model
          throw e;                                                                              // bad key etc.
        }
      }
    }
    const ex2 = exhausted(), real = fresh.filter((m) => !missing.has(m));
    // Every model that exists is out for today -> say so honestly (models that don't exist don't count).
    if (real.length && real.every((m) => ex2[m])) throw Object.assign(err("quota_daily"), { resetAt: Math.min(...real.map((m) => ex2[m])) });
    throw last || err("llm_unavailable");
  }

  async function json(prompt, opts = {}) {
    const cfg = getConfig();
    if (opts.audio && cfg.mode === "gateway") return viaGateway(prompt, opts);
    if (opts.audio) {
      if (!canAudio()) throw err("audio_unavailable");
      try {
        const out = await callGemini(cfg.gemini, cfg.gemini.main || cfg.gemini.quick, prompt, { images: [], signal: opts.signal, audio: opts.audio });
        state.lastProvider = "gemini"; return out;
      } catch (e) {
        if ((e && e.name === "AbortError") || (opts.signal && opts.signal.aborted)) throw err("cancelled");
        throw e;
      }
    }
    if (cfg.mode === "gateway") return viaGateway(prompt, opts);
    const list = usable(cfg);
    if (!list.length) throw err("no_key", "no AI key configured");
    const tier = opts.modelTier === "quick" ? "quick" : "main";
    const imgs = opts.images ? Array.from(opts.images) : [];
    let last = null;
    for (const p of list) {
      const pc = cfg[p];
      const model = pc[tier] || pc.main || pc.quick;
      const models = p === "gemini" ? [model, pc.main, pc.quick, ...GEMINI_FALLBACKS] : p === "groq" ? [model, pc.main, pc.quick] : [model];
      try {
        const ws = !!(opts.webSearch && cfg.webSearch);
        let out;
        try {
          out = await callWithModels(p, pc, models, prompt, { images: imgs, signal: opts.signal, webSearch: ws });
        } catch (e1) {
          // Free tiers may not include web search: answer without it rather than fail.
          if (!ws || (e1 && (e1.name === "AbortError" || ["invalid_json", "quota_daily", "cancelled"].includes(e1.code)))) throw e1;
          out = await callWithModels(p, pc, models, prompt, { images: imgs, signal: opts.signal, webSearch: false });
        }
        state.lastProvider = p;
        return out;
      } catch (e) {
        if ((e && e.name === "AbortError") || (opts.signal && opts.signal.aborted)) throw err("cancelled");
        if (e && e.code === "invalid_json") throw e;         // the app re-asks with a repair note
        if (e && e.code === "cancelled") throw e;
        last = e;                                           // network / key / quota -> try the next provider
      }
    }
    throw last || err("llm_unavailable");
  }

  async function test(provider) {
    if (provider === "gateway") { const out = await viaGateway('Return {"ok":true}', {}); if (!out || out.ok !== true) throw err("invalid_json"); return true; }
    const cfg = getConfig(), pc = cfg[provider];
    if (!pc || !pc.key) throw err("no_key");
    // Test exactly what chat will use: the main model first, then the quick one if set.
    for (const m of [pc.main, pc.quick].filter(Boolean)) {
      try { await CALL[provider](pc, m, 'Return {"ok":true,"hello":"أهلاً"}', { images: [], webSearch: false }); }
      catch (e) { e.model = m; throw e; }
    }
    const out = { ok: true };
    if (!out || out.ok !== true) throw err("invalid_json");
    return true;
  }

  const sample = {
    json,
    limits: async () => ({
      maxPromptBytes: 200000,
      images: { maxCount: 4, maxInputBytes: 20e6, mediaTypes: ["image/jpeg", "image/png", "image/webp", "image/gif"] },
    }),
  };

  // ---------------------------------------------------------------- encrypted local storage
  let dbPromise = null;
  function idb() {
    if (!dbPromise) dbPromise = new Promise((res, rej) => {
      const r = indexedDB.open("nasem", 1);
      r.onupgradeneeded = () => r.result.createObjectStore("kv");
      r.onsuccess = () => res(r.result);
      r.onerror = () => rej(r.error);
    });
    return dbPromise;
  }
  async function kv(mode, fn) {
    const d = await idb();
    return new Promise((res, rej) => {
      const t = d.transaction("kv", mode), s = t.objectStore("kv");
      const req = fn(s);
      t.oncomplete = () => res(req && req.result);
      t.onerror = () => rej(t.error);
    });
  }
  const kvGet = (k) => kv("readonly", (s) => s.get(k));
  const kvSet = (k, v) => kv("readwrite", (s) => s.put(v, k));
  const kvDel = (k) => kv("readwrite", (s) => s.delete(k));

  async function deviceKey() {
    let k = await kvGet("key");
    if (!k) {
      k = await crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
      await kvSet("key", k);
    }
    return k;
  }

  const db = {
    doc(path) {
      const id = "doc:" + path;
      return {
        async get() {
          const rec = await kvGet(id);
          if (!rec) return { exists: false, data: () => undefined };
          const pt = await crypto.subtle.decrypt({ name: "AES-GCM", iv: rec.iv }, await deviceKey(), rec.ct);
          const obj = JSON.parse(new TextDecoder().decode(pt));
          return { exists: true, data: () => obj };
        },
        async set(obj) {
          const iv = crypto.getRandomValues(new Uint8Array(12));
          const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, await deviceKey(), new TextEncoder().encode(JSON.stringify(obj)));
          await kvSet(id, { iv, ct });
        },
        async delete() { await kvDel(id); },
      };
    },
  };

  // Crypto-shredding: drop the device key -> anything encrypted with it is unreadable forever.
  async function shredAll() { await kvDel("doc:data/users/local/state"); await kvDel("key"); }

  const user = { id: async () => "local" };

  const downloads = {
    async save({ filename, data }) {
      const blob = data instanceof Blob ? data : new Blob([data]);
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = filename;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    },
  };

  // ---------------------------------------------------------------- cloud voices (Azure neural)
  // Real Egyptian-Arabic voices (Salma / Shakir) + English, much clearer than on-device voices.
  const TTS_KEY = "nasem-app-tts";
  const TTS_DEFAULTS = { provider: "device", azureKey: "", azureRegion: "", arVoice: "ar-EG-SalmaNeural", enVoice: "en-US-JennyNeural",
    geminiModel: "gemini-3.1-flash-tts-preview", geminiVoice: "Kore" };
  const GEMINI_VOICES = [["Kore", "Kore (ست، واضح)"], ["Aoede", "Aoede (ست، دافي)"], ["Puck", "Puck (راجل، حيوي)"], ["Charon", "Charon (راجل، هادي)"]];
  const VOICES = {
    ar: [["ar-EG-SalmaNeural", "سلمى (مصري، ست)"], ["ar-EG-ShakirNeural", "شاكر (مصري، راجل)"]],
    en: [["en-US-JennyNeural", "Jenny (US, female)"], ["en-US-GuyNeural", "Guy (US, male)"], ["en-GB-SoniaNeural", "Sonia (UK, female)"], ["en-GB-RyanNeural", "Ryan (UK, male)"]],
  };
  function getTTS() {
    let c = {}; try { c = JSON.parse(localStorage.getItem(TTS_KEY) || "{}"); } catch (e) {}
    const out = { ...TTS_DEFAULTS, ...c };
    if (!c.provider && getConfig().gemini.key) out.provider = "gemini";   // never chosen -> the human voice
    return out;
  }
  function setTTS(c) { localStorage.setItem(TTS_KEY, JSON.stringify(c)); }
  const xmlEsc = (t) => t.replace(/[<>&'"]/g, (ch) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", "'": "&apos;", '"': "&quot;" })[ch]);
  const audioCache = new Map();   // text -> object URL (small, this session only)
  const player = typeof Audio !== "undefined" ? new Audio() : null;

  async function azureAudio(text, lang, rate) {
    const c = getTTS();
    if (!c.azureKey || !c.azureRegion) throw err("no_tts_key");
    const voice = lang === "en" ? c.enVoice : c.arVoice;
    const pct = Math.round((rate - 1) * 100);
    const cacheKey = `${voice}|${pct}|${text}`;
    if (audioCache.has(cacheKey)) return audioCache.get(cacheKey);
    const ssml = `<speak version="1.0" xml:lang="${lang === "en" ? "en-US" : "ar-EG"}"><voice name="${voice}"><prosody rate="${pct >= 0 ? "+" : ""}${pct}%">${xmlEsc(text)}</prosody></voice></speak>`;
    const res = await fetch(`https://${encodeURIComponent(c.azureRegion.trim())}.tts.speech.microsoft.com/cognitiveservices/v1`, {
      method: "POST",
      headers: { "Ocp-Apim-Subscription-Key": c.azureKey.trim(), "Content-Type": "application/ssml+xml", "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3" },
      body: ssml,
    });
    if (!res.ok) throw await httpErr(res);
    const url = URL.createObjectURL(await res.blob());
    audioCache.set(cacheKey, url);
    if (audioCache.size > 30) { const [k, u] = audioCache.entries().next().value; URL.revokeObjectURL(u); audioCache.delete(k); }
    return url;
  }
  // Gemini speech: same key as the chat, Egyptian Arabic by instruction. Returns raw PCM -> wrap as WAV.
  function pcmToWav(b64, rate) {
    const pcm = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const buf = new ArrayBuffer(44 + pcm.length), v = new DataView(buf);
    const w = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
    w(0, "RIFF"); v.setUint32(4, 36 + pcm.length, true); w(8, "WAVE"); w(12, "fmt "); v.setUint32(16, 16, true);
    v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true);
    v.setUint16(32, 2, true); v.setUint16(34, 16, true); w(36, "data"); v.setUint32(40, pcm.length, true);
    new Uint8Array(buf, 44).set(pcm);
    return new Blob([buf], { type: "audio/wav" });
  }
  const MOODS = {
    warm: ["اتكلم زي صاحب مصري ودود في التلاتينات، بنبرة دافية ومبتسمة، ووقفات طبيعية بين الجمل، مش زي مذيع ولا زي آلة", "Speak like a warm, friendly person in their thirties, smiling, with natural pauses, not like a newsreader"],
    happy: ["اتكلم بفرحة حقيقية وحماس خفيف، زي حد فرحان لصاحبه، بلهجة مصرية طبيعية", "Speak with genuine joy, like someone happy for a friend"],
    calm: ["اتكلم بهدوء وثبات وجدية، واضح وبطيء شوية، صوت يطمّن، بلهجة مصرية", "Speak calmly and steadily, clear and a little slow, reassuring"],
    concerned: ["اتكلم باهتمام وقلق خفيف، زي حد بيحذّر صاحبه بحب، بلهجة مصرية طبيعية", "Speak with gentle concern, like warning a friend you care about"],
  };
  async function geminiAudio(text, lang, rate, mood = "warm") {
    const c = getTTS(), g = getConfig().gemini;
    if (!g.key) throw err("no_tts_key");
    const style = (MOODS[mood] || MOODS.warm)[lang === "en" ? 1 : 0];
    const pace = rate < 0.95 ? "، وبراحة شوية" : rate > 1.05 ? "، وبسرعة شوية" : "";
    const key = `gem|${c.geminiVoice}|${rate}|${mood}|${text}`;
    if (audioCache.has(key)) return audioCache.get(key);
    const res = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(c.geminiModel || TTS_DEFAULTS.geminiModel)}:generateContent`, {
      method: "POST", headers: { "content-type": "application/json", "x-goog-api-key": g.key },
      body: JSON.stringify({
        contents: [{ role: "user", parts: [{ text: `${style}${pace}:\n${text.slice(0, 1500)}` }] }],
        generationConfig: { responseModalities: ["AUDIO"], speechConfig: { voiceConfig: { prebuiltVoiceConfig: { voiceName: c.geminiVoice || "Kore" } } } },
      }),
    });
    if (!res.ok) throw await httpErr(res);
    const d = await res.json();
    const part = ((d.candidates && d.candidates[0] && d.candidates[0].content && d.candidates[0].content.parts) || []).find((p) => p.inlineData || p.inline_data);
    const inl = part && (part.inlineData || part.inline_data);
    if (!inl || !inl.data) throw err("upstream_error", "no audio returned");
    const mt = inl.mimeType || inl.mime_type || "";
    const rateHz = parseInt((mt.match(/rate=(\d+)/) || [])[1], 10) || 24000;
    const url = URL.createObjectURL(/wav|mpeg|mp3|ogg/.test(mt) ? b64ToBlob(inl.data, mt) : pcmToWav(inl.data, rateHz));
    audioCache.set(key, url);
    return url;
  }

  function sentences(text) {   // short chunks -> first words start fast; merge tiny ones
    const parts = String(text).split(/(?<=[.!؟?،\n])\s+/).filter(Boolean), out = []; let cur = "";
    for (const p of parts) { if (cur && (cur + " " + p).length > 160) { out.push(cur); cur = p; } else cur = cur ? cur + " " + p : p; }
    if (cur) out.push(cur);
    return out.length ? out : [String(text)];
  }
  let seq = 0, seqActive = false;
  const playUrl = (url) => new Promise((resolve, reject) => {
    player.onended = () => resolve(); player.onerror = () => reject(err("upstream_error", "audio playback failed"));
    player.pause(); player.src = url; player.play().catch(reject);
  });

  const tts = {
    VOICES, GEMINI_VOICES, get: getTTS, set: setTTS,
    /* Prefetch short phrases (e.g. call fillers) so they can play instantly later. */
    async prefetch(list, lang = "ar") { if (getTTS().provider !== "gemini") return; for (const p of list) { try { await geminiAudio(p, lang, 1, "warm"); } catch (e) { return; } } },
    playCached(text, lang = "ar") {
      const c = getTTS(), url = audioCache.get(`gem|${c.geminiVoice}|1|warm|${text}`);
      if (!url || seqActive || !player) return false;
      player.pause(); player.src = url; player.play().catch(() => {}); return true;
    },
    enabled() {
      const c = getTTS();
      if (!player) return false;
      if (c.provider === "server") return !!getGW().access;
      if (c.provider === "gemini") return !!getConfig().gemini.key;
      return c.provider === "azure" && !!c.azureKey && !!c.azureRegion;
    },
    async speak(text, lang, rate = 1, mood = "warm") {
      const c = getTTS();
      if (c.provider === "gemini") {   // play sentence by sentence, fetching the next while the current one plays
        const my = ++seq, parts = sentences(text);
        seqActive = true;
        try {
          let next = geminiAudio(parts[0], lang, rate, mood);
          for (let i = 0; i < parts.length; i++) {
            const url = await next;
            if (my !== seq) return;
            next = i + 1 < parts.length ? geminiAudio(parts[i + 1], lang, rate, mood) : null;
            if (next) next.catch(() => {});
            await playUrl(url);
            if (my !== seq) return;
          }
        } finally { if (my === seq) seqActive = false; }
        return;
      }
      let url;
      if (c.provider === "server") {
        const voice = lang === "en" ? c.enVoice : c.arVoice, key = `srv|${voice}|${rate}|${text}`;
        url = audioCache.get(key);
        if (!url) {
          const res = await gwPost("/v1/gateway/tts", { text: text.slice(0, 2000), lang, voice, rate: Math.min(2, Math.max(0.5, rate)) });
          if (!res.ok) throw err(res.status === 503 ? "tts_not_configured" : "upstream_error");
          url = URL.createObjectURL(await res.blob()); audioCache.set(key, url);
        }
      } else if (c.provider === "gemini") url = await geminiAudio(text, lang, rate);
      else url = await azureAudio(text, lang, rate);
      player.pause(); player.src = url; await player.play();
    },
    stop() { seq++; seqActive = false; if (player) player.pause(); },
    isPlaying() { return seqActive || (!!player && !player.paused && !player.ended); },
    unlock() {   // play a silent clip inside the first tap so later replies may autoplay
      if (!player || player.dataset.unlocked) return;
      player.dataset.unlocked = "1";
      player.src = "data:audio/mp3;base64,SUQzBAAAAAAAI1RTU0UAAAAPAAADTGF2ZjU4Ljc2LjEwMAAAAAAAAAAAAAAA//tQxAADB8AhSmxhIIEVCSiJrDCQBTcu3UrAIwUdkRgQbFAZC1CQEwTJ9mjRvBA4UOLD8nKVOWfh+UlK3z/177OXrfOdKl7pyn3Xf//WreyTRUoAWgBgkOAGbZHBgG1OF6zM82DWbZaUmMBptgQhGjsyYqc9ae9XFz280948NMBWInljyzsNRFLPWdnZGWrddDsjK1unuSrVN9jJsK8KuQtQCtMBjCEtImISdNKJOopIpBFpNSMbIHCSRpRR5iakjTiyzLhchUUBwCgyKiweBv/7UsQbg8isVNoMPMjAAAA0gAAABEVFGmgqK////9bP/6XCykxBTUUzLjEwMKqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq";
      player.play().then(() => player.pause()).catch(() => {});
    },
  };

  // ---------------------------------------------------------------- push reminders (end-to-end encrypted text)
  const b64urlToBytes = (s) => { const p = "=".repeat((4 - (s.length % 4)) % 4); const b = atob((s + p).replace(/-/g, "+").replace(/_/g, "/")); return Uint8Array.from(b, (c) => c.charCodeAt(0)); };
  const bytesToB64 = (buf) => { const a = new Uint8Array(buf); let s = ""; for (let i = 0; i < a.length; i += 0x8000) s += String.fromCharCode.apply(null, a.subarray(i, i + 0x8000)); return btoa(s); };
  const push = {
    supported() { return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window; },
    enabled() { try { return localStorage.getItem("nasem-push") === "1"; } catch (e) { return false; } },
    async enable() {
      if (getConfig().mode !== "gateway" || !getGW().access) throw err("push_needs_server");
      if (!this.supported()) throw err("push_unsupported");
      if ((await Notification.requestPermission()) !== "granted") throw err("push_denied");
      const kRes = await fetch(base(getGW().url) + "/v1/push/vapid-public-key");
      if (!kRes.ok) throw err("push_not_configured");
      const { public_key } = await kRes.json();
      const reg = await navigator.serviceWorker.ready;
      let sub = await reg.pushManager.getSubscription();
      if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64urlToBytes(public_key) });
      const j = sub.toJSON();
      const r2 = await gwPost("/v1/push/subscribe", { endpoint: j.endpoint, keys: { p256dh: j.keys.p256dh, auth: j.keys.auth } });
      if (!r2.ok) throw err("upstream_error");
      localStorage.setItem("nasem-push", "1");
      return true;
    },
    async disable() {
      localStorage.removeItem("nasem-push");
      try {
        const reg = await navigator.serviceWorker.ready, sub = await reg.pushManager.getSubscription();
        if (sub) { await gwPost("/v1/push/unsubscribe", { endpoint: sub.endpoint }).catch(() => {}); await sub.unsubscribe(); }
        await gwPost("/v1/push/schedule", { items: [] }).catch(() => {});
      } catch (e) {}
    },
    /* Each reminder is sealed with this phone's key; the server stores only {fire_at, ciphertext}. */
    async sync(items) {
      if (!this.enabled()) return 0;
      const key = await deviceKey(), out = [];
      for (const it of items.slice(0, 300)) {
        const iv = crypto.getRandomValues(new Uint8Array(12));
        const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, new TextEncoder().encode(JSON.stringify({ t: String(it.title).slice(0, 120), b: String(it.body || "").slice(0, 240) })));
        out.push({ client_id: String(it.id).slice(0, 80), fire_at: new Date(it.at).toISOString(), ciphertext: bytesToB64(ct), iv: bytesToB64(iv) });
      }
      const res = await gwPost("/v1/push/schedule", { items: out });
      if (!res.ok) throw err("upstream_error");
      return (await res.json()).scheduled;
    },
  };

  // "Share to Nasem": the service worker parked what was shared; hand it to the app once, then delete it.
  async function takeShared() {
    const qp = new URLSearchParams(location.search);
    if (qp.get("share")) {   // iPhone: an iOS Shortcut opens  <app>/?share=<text>
      const text = qp.get("share").slice(0, 30000);
      history.replaceState(null, "", location.pathname);
      return { title: "", text, url: "", files: [] };
    }
    if (!/[?&]shared=1/.test(location.search) || !("caches" in window)) return null;
    try {
      const cache = await caches.open("nasem-share");
      const metaRes = await cache.match("./shared/meta"); if (!metaRes) return null;
      const meta = await metaRes.json(), files = [];
      for (const f of meta.files || []) {
        const res = await cache.match(f.key);
        if (res) { const blob = await res.blob(); files.push(new File([blob], f.name || "file", { type: f.type || blob.type })); }
      }
      for (const k of await cache.keys()) await cache.delete(k);
      history.replaceState(null, "", location.pathname);
      return { title: meta.title || "", text: meta.text || "", url: meta.url || "", files };
    } catch (e) { return null; }
  }

  window.claude = { use: async (name) => ({ sample, db, user, downloads })[name] || null };
  window.NASEM_APP = { getConfig, setConfig, test, shredAll, state, PROVIDER_NAMES, tts, gateway: { get: getGW, login: gwLogin, logout: gwLogout }, canAudio, canImage, image, takeShared, push, usage, exhausted };

  if ("serviceWorker" in navigator && location.protocol.startsWith("http")) {
    window.addEventListener("load", () => navigator.serviceWorker.register("./sw.js").catch(() => {}));
  }
})();
