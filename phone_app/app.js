// Navi's phone app. It sends what you type or say to Navi on your PC and shows (and reads out) Navi's replies.
"use strict";

const $ = (id) => document.getElementById(id);
const store = {
  get(key, fallback) {
    try {
      const v = localStorage.getItem("navi." + key);
      return v === null ? fallback : JSON.parse(v);
    } catch { return fallback; }
  },
  set(key, value) {
    try {
      if (value === null) localStorage.removeItem("navi." + key);
      else localStorage.setItem("navi." + key, JSON.stringify(value));
    } catch { /* private mode: it just isn't remembered */ }
  },
};

let token = store.get("token", null);
let last = 0;              // the newest event id shown
let clearedAt = store.get("clearedAt", 0);
let busy = false, waiting = false, online = false, listening = false;
let pollAbort = null, pollRunning = false;
const options = { speak: store.get("speak", true), autosend: store.get("autosend", true) };

// ---- talking to Navi
class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

async function api(path, { method = "GET", body, signal, raw = false } = {}) {
  const headers = {};
  if (token) headers.Authorization = "Bearer " + token;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let res;
  try {
    res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), signal, cache: "no-store" });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new ApiError(0, "Can't reach your PC.");
  }
  if (!res.ok) {
    let message = "Something went wrong.";
    try { message = (await res.json()).error || message; } catch { /* not json */ }
    throw new ApiError(res.status, message);
  }
  return raw ? res : res.json();
}

// ---- pairing
function deviceName() {
  const ua = navigator.userAgent;
  if (/iPhone/.test(ua)) return "iPhone";
  if (/iPad/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) return "iPad";
  if (/Android/.test(ua)) {
    const m = ua.match(/Android [\d.]+; ([^;)]+?)(?: Build|\))/);
    return m && m[1] && m[1] !== "K" ? m[1].trim() : "Android phone";
  }
  return "Phone";
}

async function pair(code) {
  $("pair-error").textContent = "";
  try {
    const r = await api("/api/pair", { method: "POST", body: { code, name: deviceName() } });
    token = r.token;
    store.set("token", token);
    store.set("clearedAt", 0);
    clearedAt = 0;
    showChat(r.name);
  } catch (e) {
    $("pair-error").textContent = e.message;
    showPair();
  }
}

function showPair() {
  $("pair").hidden = false;
  $("chat").hidden = $("composer").hidden = $("menu-btn").hidden = true;
  setStatus();
}

function showChat(name) {
  if (name) { $("name").textContent = name; document.title = name; store.set("name", name); }
  $("pair").hidden = true;
  $("chat").hidden = $("composer").hidden = $("menu-btn").hidden = false;
  startPolling();
}

function unpaired() {
  token = null;
  store.set("token", null);
  stopPolling();
  $("log").replaceChildren();
  last = 0;
  showPair();
}

// ---- the conversation
function scrollDown() {
  const main = $("chat");
  requestAnimationFrame(() => { main.scrollTop = main.scrollHeight; });
}

function addMessage(e) {
  const div = document.createElement("div");
  div.className = "msg " + e.kind;
  div.textContent = e.text;
  div.dataset.id = e.id;
  if (e.kind === "navi") {
    div.classList.add("playable");
    div.title = "Tap to hear it again";
    div.addEventListener("click", () => { unlockAudio(); speech.play(e.id, true); });
  }
  $("log").querySelector(".empty")?.remove();
  $("log").insertBefore(div, $("log").querySelector(".typing"));
}

function showEmpty() {
  if ($("log").querySelector(".msg")) return;
  const p = document.createElement("p");
  p.className = "empty";
  p.textContent = "Ask me anything, like you would on your PC.";
  $("log").replaceChildren(p);
}

function showTyping(on) {
  const t = $("log").querySelector(".typing");
  if (on && !t) {
    const d = document.createElement("div");
    d.className = "typing";
    d.append(document.createElement("span"), document.createElement("span"), document.createElement("span"));
    $("log").append(d);
    scrollDown();
  } else if (!on && t) t.remove();
}

function setState(b, w) {
  busy = b; waiting = w;
  showTyping(busy && !waiting);
  $("stop").hidden = !busy;
  $("send").hidden = busy && !waiting && !$("say").value;
  if (waiting) $("say").placeholder = "Type your answer...";
  else $("say").placeholder = "Ask " + ($("name").textContent || "Navi") + "...";
  setStatus();
}

function setStatus() {
  const orb = $("orb");
  orb.className = "orb";
  let text;
  if (!token) text = "";
  else if (!online) { text = "Can't reach your PC"; orb.classList.add("offline"); }
  else if (listening) { text = "Listening..."; orb.classList.add("listening"); }
  else if (waiting) { text = "Your turn"; orb.classList.add("waiting"); }
  else if (busy) { text = "Thinking..."; orb.classList.add("busy"); }
  else text = "Online";
  $("status").textContent = text;
}

function handle(events, live) {
  for (const e of events) {
    last = Math.max(last, e.id);
    if (e.id <= clearedAt) continue;
    if (e.kind === "state") { if (live) setState(e.busy, e.waiting); continue; }
    if (!["you", "navi", "system"].includes(e.kind)) continue;
    if ($("log").querySelector(`[data-id="${e.id}"]`)) continue;
    addMessage(e);
    if (e.kind === "navi" && live && options.speak) speech.queue(e.id);
  }
  showEmpty();
  scrollDown();
}

// ---- getting replies: one long request at a time that Navi answers as soon as there's something new
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function startPolling() {
  if (pollRunning) return;
  pollRunning = true;
  let backoff = 1000, first = true;
  while (token && pollRunning) {
    pollAbort = new AbortController();
    const timer = setTimeout(() => pollAbort.abort(), 40000);
    try {
      const data = await api(`/api/events?after=${first ? 0 : last}${first ? "" : "&wait=1"}`, { signal: pollAbort.signal });
      if (data.last < last) { // Navi was restarted: its old messages are gone
        $("log").replaceChildren();
        last = 0; clearedAt = 0; store.set("clearedAt", 0);
      }
      online = true;
      handle(data.events, !first);
      setState(data.busy, data.waiting);
      first = false;
      backoff = 1000;
    } catch (e) {
      if (e.status === 401) { unpaired(); break; }
      if (e.name !== "AbortError") {
        online = false;
        setStatus();
        await sleep(backoff);
        backoff = Math.min(backoff * 2, 15000);
      }
    } finally {
      clearTimeout(timer);
    }
  }
  pollRunning = false;
}

function stopPolling() {
  pollRunning = false;
  pollAbort?.abort();
}

function restartPolling() {
  // phones freeze background tabs, so a request left hanging is replaced when you come back
  if (!token) return;
  stopPolling();
  setTimeout(startPolling, 50);
}

async function say(text) {
  text = text.trim();
  if (!text) return;
  unlockAudio();
  speech.stop();
  $("say").value = "";
  try {
    await api("/api/say", { method: "POST", body: { text } });
    if (!busy) setState(true, false);
  } catch (e) {
    if (e.status === 401) return unpaired();
    $("say").value = text; // so nothing you typed is lost
    const div = document.createElement("div");
    div.className = "msg system";
    div.textContent = e.message;
    $("log").append(div);
    scrollDown();
  }
}

// ---- Navi's voice: the same voice as on the PC, made by Navi and played here
let audioCtx = null;
function unlockAudio() {
  // phones only allow sound after a tap, so the first tap opens the speaker for later replies
  try {
    if (!audioCtx) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      audioCtx = new Ctx();
    }
    if (audioCtx.state === "suspended") audioCtx.resume();
    const b = audioCtx.createBuffer(1, 1, 22050), s = audioCtx.createBufferSource();
    s.buffer = b; s.connect(audioCtx.destination); s.start(0);
  } catch { /* no sound on this browser */ }
}

const speech = {
  waitingIds: [], source: null, playingId: null, cache: new Map(),
  queue(id) { this.waitingIds.push(id); if (!this.source) this.next(); },
  async play(id, now) {
    if (now) { this.stop(); }
    this.waitingIds.unshift(id);
    if (!this.source) this.next();
  },
  stop() {
    this.waitingIds = [];
    try { this.source?.stop(); } catch { /* already done */ }
    this.source = null;
    this.mark(null);
  },
  mark(id) {
    document.querySelectorAll(".msg.playing").forEach((m) => m.classList.remove("playing"));
    if (id) document.querySelector(`.msg[data-id="${id}"]`)?.classList.add("playing");
    this.playingId = id;
  },
  async next() {
    const id = this.waitingIds.shift();
    if (!id || !audioCtx) return;
    try {
      let buf = this.cache.get(id);
      if (!buf) {
        const res = await api(`/api/speech?id=${id}`, { raw: true });
        buf = await audioCtx.decodeAudioData(await res.arrayBuffer());
        this.cache.set(id, buf);
        if (this.cache.size > 20) this.cache.delete(this.cache.keys().next().value);
      }
      if (this.source) return; // something else started meanwhile
      const s = audioCtx.createBufferSource();
      s.buffer = buf;
      s.connect(audioCtx.destination);
      s.onended = () => { if (this.source === s) { this.source = null; this.mark(null); this.next(); } };
      this.source = s;
      this.mark(id);
      s.start(0);
    } catch {
      this.source = null;
      this.mark(null);
      this.next();
    }
  },
};

// ---- talking instead of typing (needs HTTPS, so it shows up on the Tailscale address)
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec = null;
function setupMic() {
  if (!Recognition || !window.isSecureContext) {
    $("hint").textContent = "Tip: tap the microphone on your keyboard to talk. " +
      (window.isSecureContext ? "" : "A talk button appears here when you open Navi through Tailscale (see Settings > Phone app on your PC).");
    $("hint").hidden = false;
    return;
  }
  $("mic").hidden = false;
  $("mic").addEventListener("click", () => {
    unlockAudio();
    if (listening) { rec?.stop(); return; }
    speech.stop();
    rec = new Recognition();
    rec.lang = navigator.language || "en-US";
    rec.interimResults = true;
    rec.continuous = false;
    let finalText = "";
    rec.onresult = (ev) => {
      let interim = "";
      finalText = "";
      for (const r of ev.results) (r.isFinal ? (finalText += r[0].transcript) : (interim += r[0].transcript));
      $("say").value = (finalText + interim).trim();
    };
    rec.onerror = (ev) => {
      if (ev.error === "not-allowed" || ev.error === "service-not-allowed")
        $("hint").textContent = "Allow the microphone for this app in your phone's settings to talk to Navi.", $("hint").hidden = false;
    };
    rec.onend = () => {
      listening = false;
      $("mic").classList.remove("on");
      setStatus();
      if (options.autosend && finalText.trim()) say(finalText);
    };
    try {
      rec.start();
      listening = true;
      $("mic").classList.add("on");
      setStatus();
    } catch { listening = false; }
  });
}

function pairFromLink() {
  // the QR code opens .../#pair=CODE
  const m = location.hash.match(/pair=([A-Za-z0-9-]+)/);
  if (!m) return false;
  history.replaceState(null, "", location.pathname); // the code is used up either way, so don't keep it around
  pair(m[1]);
  return true;
}

// ---- wiring
function init() {
  $("name").textContent = store.get("name", "Navi");
  $("pair-form").addEventListener("submit", (ev) => { ev.preventDefault(); pair($("pair-code").value); });
  $("say-form").addEventListener("submit", (ev) => { ev.preventDefault(); say($("say").value); });
  $("say").addEventListener("input", () => setState(busy, waiting));
  $("stop").addEventListener("click", () => { speech.stop(); api("/api/stop", { method: "POST" }).catch(() => {}); });
  $("chips").addEventListener("click", (ev) => { const b = ev.target.closest("button"); if (b) say(b.dataset.say); });

  $("menu-btn").addEventListener("click", () => {
    $("opt-speak").checked = options.speak;
    $("opt-autosend").checked = options.autosend;
    $("opt-autosend").closest(".row").hidden = $("mic").hidden;
    $("sheet").hidden = false;
  });
  $("close-sheet").addEventListener("click", () => { $("sheet").hidden = true; });
  $("sheet").addEventListener("click", (ev) => { if (ev.target === $("sheet")) $("sheet").hidden = true; });
  $("opt-speak").addEventListener("change", (ev) => { options.speak = ev.target.checked; store.set("speak", options.speak); if (!options.speak) speech.stop(); });
  $("opt-autosend").addEventListener("change", (ev) => { options.autosend = ev.target.checked; store.set("autosend", options.autosend); });
  $("clear-btn").addEventListener("click", () => {
    clearedAt = last; store.set("clearedAt", last);
    $("log").replaceChildren(); showEmpty(); showTyping(busy && !waiting);
    $("sheet").hidden = true;
  });
  $("unpair-btn").addEventListener("click", async () => {
    try { await api("/api/unpair", { method: "POST" }); } catch { /* removed here anyway */ }
    $("sheet").hidden = true;
    unpaired();
  });

  document.addEventListener("visibilitychange", () => { if (!document.hidden) restartPolling(); });
  window.addEventListener("online", restartPolling);
  window.addEventListener("pageshow", (ev) => { if (ev.persisted) restartPolling(); });

  if ("serviceWorker" in navigator && window.isSecureContext) navigator.serviceWorker.register("sw.js").catch(() => {});
  setupMic();

  window.addEventListener("hashchange", pairFromLink);
  if (pairFromLink()) return;
  if (token) {
    api("/api/hello").then((r) => showChat(r.name)).catch((e) => (e.status === 401 ? unpaired() : showChat()));
  } else {
    showPair();
  }
}

init();
