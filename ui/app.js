/* J.A.R.V.I.S. HUD */
const $ = (s) => document.querySelector(s);
const TOOL_NAMES = {
  open_application: "APPS", close_application: "PROC", open_website: "WEB", system_status: "DIAG",
  volume_control: "AUDIO", media_control: "MEDIA", look_at_screen: "VISION", power_action: "POWER",
  get_weather: "WEATHER", set_reminder: "TIMERS", list_reminders: "TIMERS", cancel_reminder: "TIMERS",
  find_files: "FILES", open_path: "FILES", clipboard: "CLIPBOARD", web_search: "INTEL",
  play_music: "MEDIA", run_protocol: "PROTOCOLS", list_protocols: "PROTOCOLS", create_protocol: "PROTOCOLS",
  delete_protocol: "PROTOCOLS", remember: "MEMORY", forget: "MEMORY", get_calendar: "CALENDAR",
  window_control: "WINDOWS", type_text: "KEYBOARD", press_keys: "KEYBOARD", youtube_music: "MEDIA",
  browser_open: "BROWSER", browser_snapshot: "BROWSER", browser_click: "BROWSER", browser_type: "BROWSER",
  browser_press: "BROWSER", browser_scroll: "BROWSER", browser_read: "BROWSER", browser_back: "BROWSER",
  browser_look: "BROWSER", browser_window: "BROWSER", restore_session: "APPS", recent_apps: "APPS",
  place_window: "SCREENS", monitors: "SCREENS", save_layout: "SCREENS", apply_layout: "SCREENS",
  obs: "OBS", click_in_app: "CONTROL", app_buttons: "CONTROL",
};
let SETTINGS = { sfx: true, voice_fx: true };
const STATES = {
  idle:        ["STANDBY",     "Мікрофон неактивний — пишіть команду нижче"],
  listening:   ["ONLINE",      "Слухаю вас — просто говоріть · «Джарвіс, спи» — пауза · Ctrl+Alt+Space — сховати"],
  listening_wake: ["ONLINE",   "Скажіть «Джарвіс» · Ctrl+Alt+J — говорити · Ctrl+Alt+Space — сховати"],
  listening_off: ["ONLINE",    "Скажіть «Hey Jarvis» або натисніть Ctrl+Alt+J"],
  awake:       ["LISTENING",   "Слухаю, сер…"],
  recognizing: ["PROCESSING",  "Розпізнаю мову…"],
  thinking:    ["ANALYZING",   "Обробляю запит…"],
  speaking:    ["RESPONDING",  ""],
  mic_error:   ["MIC OFFLINE", "Мікрофон не знайдено — використовуйте текстове введення"],
  muted:       ["MUTED",       "Мікрофон вимкнено"],
};

/* ---------- міст до Python ---------- */
const hasPy = () => window.pywebview && window.pywebview.api;
async function api(name, ...args) {
  if (hasPy()) return window.pywebview.api[name](...args);
  return DEMO[name] ? DEMO[name](...args) : null;   // режим перегляду в браузері
}

/* ---------- стан ---------- */
let state = "idle";
let muted = false;
let audioLevel = 0;
let micLevel = 0;
let replyEl = null, replyTarget = null, replyQueue = "", replyTimer = null;
let audioCtx, analyser, freq, fxIn, dryIn, currentSrc;

const HUD = {
  setState(s) {
    if (muted && (s === "listening" || s === "awake")) s = "muted";
    const prev = state;
    state = s;
    document.body.dataset.state = s;
    const key = s !== "listening" ? s : SETTINGS.listen_mode === "wake"
      ? (SETTINGS.wake_engine === "offline" ? "listening_off" : "listening_wake") : "listening";
    const [big, hint] = STATES[key] || [s.toUpperCase(), ""];
    $("#state-big").textContent = big;
    $("#state-hint").textContent = hint;
    $("#status-text").textContent = big;
    $("#btn-mic").classList.toggle("on", s === "awake" || s === "recognizing");
    $("#r-mic").textContent = s === "mic_error" ? "ERR" : muted ? "OFF" : "OK";
    if (s === "awake" && prev !== "awake") chime();
  },
  heard(text, ignored) {
    const h = $("#heard");
    h.textContent = "«" + text + "»" + (ignored ? "  — почув, але без «Джарвіс»" : "");
    h.classList.toggle("ignored", !!ignored);
    h.classList.add("show");
    clearTimeout(h._t);
    h._t = setTimeout(() => h.classList.remove("show"), 5000);
  },
  addMessage(who, text) {
    const el = document.createElement("div");
    el.className = "msg " + who;
    const t = new Date().toLocaleTimeString("uk-UA", { hour: "2-digit", minute: "2-digit" });
    el.innerHTML = `<div class="who">${who === "user" ? "ВИ" : "J.A.R.V.I.S."}<em>${t}</em></div>`;
    const body = document.createElement("div");
    el.appendChild(body);
    $("#log").appendChild(el);
    if (who === "jarvis") typeText(body, text); else body.textContent = text;
    $("#log").scrollTop = 1e9;
  },
  beginReply() {
    const el = document.createElement("div");
    el.className = "msg jarvis streaming";
    const t = new Date().toLocaleTimeString("uk-UA", { hour: "2-digit", minute: "2-digit" });
    el.innerHTML = `<div class="who">J.A.R.V.I.S.<em>${t}</em></div><div class="body"></div>`;
    $("#log").appendChild(el);
    replyEl = replyTarget = el.querySelector(".body");
    replyQueue = "";
  },
  replyText(t) {
    if (!replyEl) HUD.beginReply();
    replyQueue += (replyEl.textContent || replyQueue ? " " : "") + t;
    if (!replyTimer) replyTimer = setInterval(() => {
      if (!replyQueue) { clearInterval(replyTimer); replyTimer = null; return; }
      replyTarget.textContent += replyQueue.slice(0, 3);
      replyQueue = replyQueue.slice(3);
      $("#log").scrollTop = 1e9;
    }, 16);
  },
  endReply() {
    const el = replyEl;
    replyEl = null;
    if (el) {
      el.parentElement.classList.remove("streaming");
      setTimeout(() => { if (!el.textContent.trim() && !(replyTarget === el && replyQueue)) el.parentElement.remove(); }, 400);
    }
  },
  toolUsed(name) {
    const label = TOOL_NAMES[name] || name.toUpperCase();
    document.querySelectorAll("#tools span").forEach((s) => {
      if (s.textContent === label) { s.classList.add("active"); setTimeout(() => s.classList.remove("active"), 2500); }
    });
  },
  async playAudio(b64) {
    try {
      ensureAudio();
      if (audioCtx.state === "suspended") await audioCtx.resume();
      const bin = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
      const buf = await audioCtx.decodeAudioData(bin.buffer);
      const src = audioCtx.createBufferSource();
      src.buffer = buf;
      src.connect(SETTINGS.voice_fx ? fxIn : dryIn);
      src.onended = () => { if (currentSrc === src) currentSrc = null; api("audio_done"); };
      currentSrc = src;
      src.start();
    } catch (e) {
      console.error(e);
      api("audio_done");
    }
  },
  reminder(text) {
    const t = $("#toast");
    t.className = "toast warn";
    t.innerHTML = `<b>НАГАДУВАННЯ</b>${escapeHtml(text)}`;
    chime(); setTimeout(chime, 350);
    setTimeout(() => t.classList.add("hidden"), 9000);
  },
  openSettings() { openSettings(); },
  stopAudio() {
    try { if (currentSrc) currentSrc.stop(); } catch (e) { api("audio_done"); }
    sfx("stop");
  },
  holo(kind, data) { showHolo(kind, data || {}); },
  mic(v) { micLevel = v || 0; },
  systems(r) {
    const set = (id, ok, txt) => { const el = $(id); el.textContent = txt || (ok ? "OK" : "ERR"); el.classList.toggle("bad", !ok); };
    set("#r-ai", r.ai, r.ai ? "ONLINE" : "OFFLINE");
    set("#r-vox", r.voice, r.voice ? "UA" : "OFF");
    set("#r-mic", r.mic, r.mic ? "OK" : "ERR");
    set("#r-net", r.net, r.net ? "OK" : "DOWN");
    if (!r.ai && r.ai_info) $("#r-ai").title = r.ai_info;
  },
  alert(text, level) {
    const t = $("#toast");
    t.className = "toast " + (level || "warn");
    const head = level === "crit" ? "КРИТИЧНЕ ПОПЕРЕДЖЕННЯ" : level === "info" ? "СИСТЕМНЕ ПОВІДОМЛЕННЯ" : "ПОПЕРЕДЖЕННЯ";
    t.innerHTML = `<b>${head}</b>${escapeHtml(text)}`;
    sfx(level === "crit" ? "alarm" : "alert");
    if (level === "crit") { document.body.classList.add("red-alert"); setTimeout(() => document.body.classList.remove("red-alert"), 6000); }
    clearTimeout(t._t);
    t._t = setTimeout(() => t.classList.add("hidden"), 9000);
  },
};
window.HUD = HUD;

function escapeHtml(s) { return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

function typeText(el, text) {
  let i = 0;
  const id = setInterval(() => {
    i += 3;
    el.textContent = text.slice(0, i);
    $("#log").scrollTop = 1e9;
    if (i >= text.length) clearInterval(id);
  }, 16);
}

/* ---------- звук ---------- */
function ensureAudio() {
  if (audioCtx) return;
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 256;
  analyser.smoothingTimeConstant = 0.75;
  freq = new Uint8Array(analyser.frequencyBinCount);
  analyser.connect(audioCtx.destination);
  dryIn = audioCtx.createGain();
  dryIn.connect(analyser);
  buildVoiceFx();
}

/* Кінематографічна обробка голосу: «радіо-присутність» + легка реверберація, як голос у шоломі */
function buildVoiceFx() {
  const a = audioCtx;
  fxIn = a.createGain();
  const low = a.createBiquadFilter(); low.type = "highpass"; low.frequency.value = 110;
  const presence = a.createBiquadFilter(); presence.type = "peaking"; presence.frequency.value = 3200; presence.Q.value = 0.9; presence.gain.value = 4;
  const air = a.createBiquadFilter(); air.type = "highshelf"; air.frequency.value = 8000; air.gain.value = 2.5;
  const comp = a.createDynamicsCompressor(); comp.threshold.value = -20; comp.ratio.value = 3.5; comp.attack.value = 0.004; comp.release.value = 0.18;
  const conv = a.createConvolver();
  const len = Math.floor(a.sampleRate * 0.55), ir = a.createBuffer(2, len, a.sampleRate);
  for (let ch = 0; ch < 2; ch++) {
    const d = ir.getChannelData(ch);
    for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, 3.2);
  }
  conv.buffer = ir;
  const wet = a.createGain(); wet.gain.value = 0.16;
  const dry = a.createGain(); dry.gain.value = 1.0;
  fxIn.connect(low).connect(presence).connect(air).connect(comp);
  comp.connect(dry).connect(analyser);
  comp.connect(conv).connect(wet).connect(analyser);
}

/* Звукові ефекти інтерфейсу — синтезовані, без файлів */
function tone(f1, f2, dur, type = "sine", vol = 0.08, delay = 0) {
  const a = audioCtx, t = a.currentTime + delay;
  const o = a.createOscillator(), g = a.createGain();
  o.type = type;
  o.frequency.setValueAtTime(f1, t);
  o.frequency.exponentialRampToValueAtTime(f2, t + dur);
  g.gain.setValueAtTime(0, t);
  g.gain.linearRampToValueAtTime(vol, t + 0.015);
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  o.connect(g).connect(a.destination);
  o.start(t); o.stop(t + dur + 0.05);
}
function sfx(kind) {
  if (!SETTINGS.sfx) return;
  try {
    ensureAudio();
    if (kind === "boot") { tone(60, 420, 1.6, "sawtooth", 0.035); tone(220, 880, 1.2, "sine", 0.05, 0.4); tone(1760, 1760, 0.25, "sine", 0.05, 1.6); }
    else if (kind === "card") { tone(1400, 2200, 0.08, "square", 0.025); tone(2200, 2600, 0.06, "sine", 0.03, 0.06); }
    else if (kind === "alert") { tone(740, 740, 0.18, "triangle", 0.09); tone(988, 988, 0.22, "triangle", 0.09, 0.2); }
    else if (kind === "alarm") { for (let i = 0; i < 3; i++) tone(880, 440, 0.28, "sawtooth", 0.05, i * 0.32); }
    else if (kind === "stop") tone(900, 300, 0.15, "sine", 0.05);
    else if (kind === "step") tone(1600, 1600, 0.05, "square", 0.02);
    else if (kind === "click") tone(2400, 1800, 0.03, "square", 0.015);
  } catch (e) {}
}
function chime() {
  if (!SETTINGS.sfx) return;
  try {
    ensureAudio();
    const now = audioCtx.currentTime;
    [880, 1320].forEach((f, i) => {
      const o = audioCtx.createOscillator(), g = audioCtx.createGain();
      o.type = "sine"; o.frequency.value = f;
      g.gain.setValueAtTime(0, now + i * 0.09);
      g.gain.linearRampToValueAtTime(0.12, now + i * 0.09 + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, now + i * 0.09 + 0.35);
      o.connect(g).connect(audioCtx.destination);
      o.start(now + i * 0.09); o.stop(now + i * 0.09 + 0.4);
    });
  } catch (e) {}
}

/* ---------- РЕАКТОР ---------- */
const cv = $("#reactor");
const ctx = cv.getContext("2d");
let W = 0, H = 0, DPR = 1;
function resize() {
  DPR = window.devicePixelRatio || 1;
  W = cv.clientWidth; H = cv.clientHeight;
  cv.width = W * DPR; cv.height = H * DPR;
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
}
window.addEventListener("resize", resize);

const PAL = {
  cyan: [63, 240, 255], amber: [255, 179, 64], red: [255, 59, 92], white: [220, 252, 255],
};
let col = [...PAL.cyan];
const rgba = (c, a) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`;
let spin = 0, speed = 1, energy = 0;
const bars = new Array(96).fill(0);

function arc(r, a0, a1, w, c, a, glow = 0) {
  ctx.beginPath();
  ctx.arc(0, 0, r, a0, a1);
  ctx.lineWidth = w;
  ctx.strokeStyle = rgba(c, a);
  ctx.shadowBlur = glow; ctx.shadowColor = rgba(c, 0.9);
  ctx.stroke();
  ctx.shadowBlur = 0;
}

function frame(t) {
  requestAnimationFrame(frame);
  if (!W) resize();
  const time = t / 1000;

  // цільовий колір і швидкість від стану
  const target = state === "thinking" ? PAL.amber : (state === "mic_error" ? PAL.red : state === "awake" ? PAL.white : PAL.cyan);
  for (let i = 0; i < 3; i++) col[i] += (target[i] - col[i]) * 0.06;
  const tSpeed = state === "thinking" ? 4.5 : state === "awake" || state === "recognizing" ? 2.2 : state === "speaking" ? 1.6 : 0.6;
  speed += (tSpeed - speed) * 0.05;
  spin += 0.004 * speed;

  // рівень звуку
  let lvl = 0;
  if (analyser && state === "speaking") {
    analyser.getByteFrequencyData(freq);
    let s = 0; for (let i = 2; i < 60; i++) s += freq[i];
    lvl = s / (58 * 255);
  }
  const listeningNow = state === "awake" || state === "recognizing" || state === "listening";
  if (listeningNow) lvl = micLevel * (state === "listening" ? 0.45 : 1);
  audioLevel += (lvl - audioLevel) * 0.3;
  const tEnergy = state === "speaking" ? 0.4 + audioLevel * 1.6 : state === "awake" ? 0.55 + audioLevel : state === "thinking" ? 0.45 : 0.18 + audioLevel * 0.5;
  energy += (tEnergy - energy) * 0.08;

  ctx.clearRect(0, 0, W, H);
  const R = Math.min(W, H) * 0.42;
  ctx.save();
  ctx.translate(W / 2, H / 2);

  // фонове сяйво
  const g = ctx.createRadialGradient(0, 0, 0, 0, 0, R * 1.25);
  g.addColorStop(0, rgba(col, 0.16 + energy * 0.12));
  g.addColorStop(0.45, rgba(col, 0.05));
  g.addColorStop(1, rgba(col, 0));
  ctx.fillStyle = g;
  ctx.fillRect(-R * 1.3, -R * 1.3, R * 2.6, R * 2.6);

  // 1. зовнішня шкала
  ctx.save();
  ctx.rotate(spin * 0.3);
  for (let i = 0; i < 180; i++) {
    const a = (i / 180) * Math.PI * 2;
    const long = i % 15 === 0, mid = i % 5 === 0;
    const r1 = R * 1.0, r2 = R * (long ? 0.94 : mid ? 0.965 : 0.98);
    ctx.beginPath();
    ctx.moveTo(Math.cos(a) * r1, Math.sin(a) * r1);
    ctx.lineTo(Math.cos(a) * r2, Math.sin(a) * r2);
    ctx.strokeStyle = rgba(col, long ? 0.9 : mid ? 0.55 : 0.25);
    ctx.lineWidth = long ? 2 : 1;
    ctx.stroke();
  }
  ctx.restore();

  // 2. сегментні дуги (обертаються навпаки)
  ctx.save();
  ctx.rotate(-spin * 0.8);
  for (let i = 0; i < 3; i++) {
    const s = (i / 3) * Math.PI * 2;
    arc(R * 0.89, s, s + Math.PI * 0.45, 5, col, 0.85, 14);
    arc(R * 0.89, s + Math.PI * 0.5, s + Math.PI * 0.56, 5, col, 0.5);
  }
  ctx.restore();

  // 3. тонке кільце з пунктиром
  ctx.save();
  ctx.rotate(spin * 1.4);
  ctx.setLineDash([2, 7]);
  arc(R * 0.82, 0, Math.PI * 2, 2, col, 0.5);
  ctx.setLineDash([]);
  arc(R * 0.78, 0.2, 1.4, 1.5, col, 0.6);
  arc(R * 0.78, 2.4, 4.0, 1.5, col, 0.6);
  arc(R * 0.78, 4.6, 5.6, 1.5, col, 0.6);
  ctx.restore();

  // 4. радіальний еквалайзер
  const n = bars.length;
  for (let i = 0; i < n; i++) {
    let v;
    if (state === "speaking" && analyser) {
      const idx = 2 + Math.floor((i < n / 2 ? i : n - i) * 0.9);
      v = freq[idx] / 255;
    } else if (listeningNow && audioLevel > 0.03) {
      // голос користувача: хвиля, що біжить по колу з амплітудою від мікрофона
      v = audioLevel * (0.55 + 0.45 * Math.sin(time * 14 + i * 0.7) * Math.sin(i * 0.23 + time * 3));
    } else {
      v = (Math.sin(time * 2 + i * 0.45) * 0.5 + 0.5) * 0.12 * (0.5 + energy) + (state === "thinking" ? (Math.sin(time * 9 + i) * 0.5 + 0.5) * 0.18 : 0);
    }
    bars[i] += (v - bars[i]) * 0.35;
  }
  ctx.save();
  ctx.rotate(-Math.PI / 2);
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2;
    const r1 = R * 0.6, r2 = r1 + R * (0.02 + bars[i] * 0.15);
    ctx.beginPath();
    ctx.moveTo(Math.cos(a) * r1, Math.sin(a) * r1);
    ctx.lineTo(Math.cos(a) * r2, Math.sin(a) * r2);
    ctx.strokeStyle = rgba(col, 0.45 + bars[i]);
    ctx.lineWidth = 2.4;
    ctx.stroke();
  }
  ctx.restore();

  // 5. внутрішнє кільце реактора з сегментами
  ctx.save();
  ctx.rotate(spin * 2.2);
  arc(R * 0.55, 0, Math.PI * 2, 1.2, col, 0.5);
  for (let i = 0; i < 10; i++) {
    const a = (i / 10) * Math.PI * 2;
    arc(R * 0.47, a + 0.05, a + Math.PI * 2 / 10 - 0.05, R * 0.075, col, 0.18 + energy * 0.35, 8);
  }
  ctx.restore();

  // трикутник Mark VI
  ctx.save();
  ctx.rotate(-spin * 0.5);
  ctx.beginPath();
  for (let i = 0; i < 3; i++) {
    const a = -Math.PI / 2 + (i / 3) * Math.PI * 2;
    ctx[i ? "lineTo" : "moveTo"](Math.cos(a) * R * 0.36, Math.sin(a) * R * 0.36);
  }
  ctx.closePath();
  ctx.strokeStyle = rgba(col, 0.55 + energy * 0.4);
  ctx.lineWidth = 3;
  ctx.shadowBlur = 18; ctx.shadowColor = rgba(col, 1);
  ctx.stroke();
  ctx.restore();

  // 6. ядро
  const coreR = R * (0.2 + energy * 0.05 + audioLevel * 0.06);
  const cg = ctx.createRadialGradient(0, 0, 0, 0, 0, coreR * 1.8);
  cg.addColorStop(0, "rgba(255,255,255,0.95)");
  cg.addColorStop(0.25, rgba(col, 0.9));
  cg.addColorStop(0.6, rgba(col, 0.25));
  cg.addColorStop(1, rgba(col, 0));
  ctx.fillStyle = cg;
  ctx.beginPath(); ctx.arc(0, 0, coreR * 1.8, 0, Math.PI * 2); ctx.fill();
  arc(coreR, 0, Math.PI * 2, 2, col, 0.9, 20);

  // сканер-промінь
  ctx.save();
  ctx.rotate(spin * 3);
  const sg = ctx.createLinearGradient(0, 0, R * 0.98, 0);
  sg.addColorStop(0, rgba(col, 0));
  sg.addColorStop(1, rgba(col, 0.35));
  ctx.beginPath(); ctx.moveTo(0, 0); ctx.arc(0, 0, R * 0.98, -0.12, 0); ctx.closePath();
  ctx.fillStyle = sg; ctx.fill();
  ctx.restore();

  ctx.restore();
}
requestAnimationFrame(frame);

/* ---------- ТЕЛЕМЕТРІЯ ---------- */
const netHist = { down: new Array(60).fill(0), up: new Array(60).fill(0) };
function setGauge(id, pct, label) {
  const el = $(id);
  const c = el.querySelector(".g-fg");
  const v = pct == null ? 0 : Math.max(0, Math.min(100, pct));
  c.style.strokeDashoffset = 314.16 * (1 - v / 100);
  el.querySelector(".g-val").innerHTML = label != null ? label : `${Math.round(v)}<small>%</small>`;
  el.classList.toggle("warn", v >= 75 && v < 90 && id !== "#g-bat");
  el.classList.toggle("crit", v >= 90 && id !== "#g-bat");
}
function fmtRate(k) { return k > 1024 ? (k / 1024).toFixed(1) + " МБ/с" : k.toFixed(0) + " КБ/с"; }
function fmtUp(s) { const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60); return (d ? d + "д " : "") + h + "г " + m + "хв"; }

function drawNet() {
  const c = $("#net"), x = c.getContext("2d");
  const w = c.width = c.clientWidth * DPR, h = c.height = 70 * DPR;
  const max = Math.max(50, ...netHist.down, ...netHist.up);
  [["down", "rgba(63,240,255,"], ["up", "rgba(255,179,64,"]].forEach(([k, cs]) => {
    x.beginPath();
    netHist[k].forEach((v, i) => {
      const px = (i / 59) * w, py = h - (v / max) * (h - 6) - 3;
      i ? x.lineTo(px, py) : x.moveTo(px, py);
    });
    x.strokeStyle = cs + "0.9)"; x.lineWidth = 1.5 * DPR; x.stroke();
    x.lineTo(w, h); x.lineTo(0, h); x.closePath(); x.fillStyle = cs + "0.08)"; x.fill();
  });
}

async function poll() {
  try {
    const s = await api("stats");
    if (s) {
      setGauge("#g-cpu", s.cpu);
      setGauge("#g-ram", s.ram);
      setGauge("#g-disk", s.disk);
      if (s.battery != null) setGauge("#g-bat", s.battery, `${s.battery}<small>%${s.plugged ? "⚡" : ""}</small>`);
      else setGauge("#g-bat", 100, `AC`);
      $("#r-pwr").textContent = s.battery != null ? s.battery + "%" : "AC";
      const cores = $("#cores");
      if (cores.children.length !== s.cores.length) cores.innerHTML = s.cores.map(() => "<i></i>").join("");
      s.cores.forEach((v, i) => (cores.children[i].style.height = Math.max(4, v) + "%"));
      netHist.down.push(s.down); netHist.down.shift();
      netHist.up.push(s.up); netHist.up.shift();
      drawNet();
      $("#net-down").textContent = fmtRate(s.down);
      $("#net-up").textContent = fmtRate(s.up);
      $("#ram-abs").textContent = `${s.ram_used} / ${s.ram_total} ГБ`;
      $("#procs").textContent = s.procs;
      $("#uptime").textContent = fmtUp(s.uptime);
      if (s.usage) {
        $("#api-cost").textContent = "$" + s.usage.cost.toFixed(s.usage.cost < 1 ? 3 : 2) + " · " + s.usage.requests + " зап.";
        $("#api-cost").title = `За місяць: $${s.usage.month} · з кешу: ${s.usage.cached_pct}% вхідних токенів`;
      }
      const rem = $("#reminders");
      rem.innerHTML = s.reminders.length
        ? s.reminders.map((r) => `<div class="rem"><b>${r.when.slice(11, 16)}</b><span>${escapeHtml(r.text)}</span></div>`).join("")
        : '<div class="empty">Нагадувань немає</div>';
    }
  } catch (e) { console.error(e); }
  setTimeout(poll, 1000);
}

function tickClock() {
  const d = new Date();
  $("#clock-time").textContent = d.toLocaleTimeString("uk-UA");
  $("#clock-date").textContent = d.toLocaleDateString("uk-UA", { weekday: "long", day: "numeric", month: "long" });
}
setInterval(tickClock, 1000); tickClock();

$("#tools").innerHTML = [...new Set(Object.values(TOOL_NAMES))].map((n) => `<span>${n}</span>`).join("");

/* ---------- ВВЕДЕННЯ ---------- */
function send() {
  const v = $("#cmd").value.trim();
  if (!v) return;
  ensureAudio();
  $("#cmd").value = "";
  api("send_text", v);
}
$("#btn-send").onclick = send;
$("#cmd").addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
$("#btn-mic").onclick = () => { ensureAudio(); api("mic"); };
$("#btn-mute").onclick = async () => {
  muted = await api("toggle_mute");
  $("#btn-mute").classList.toggle("off", !!muted);
  HUD.setState(muted ? "muted" : "listening");
};
document.addEventListener("click", () => { ensureAudio(); if (audioCtx.state === "suspended") audioCtx.resume(); }, { once: true });

/* ---------- НАЛАШТУВАННЯ ---------- */
const PROVIDERS = {
  openai: { key: "openai_key", model: "openai_model", def: "gpt-6-luna", ph: "sk-…",
    models: ["gpt-6-luna", "gpt-6.1-sol"],
    hint: "platform.openai.com/api-keys → «Create new secret key». Потрібен баланс на platform.openai.com → Billing (підписка ChatGPT Plus сюди не входить). gpt-6-luna — найшвидша й найдешевша: ≈0,05 цента за відповідь." },
  gemini: { key: "gemini_key", model: "gemini_model", def: "gemini-3.8-flash", ph: "AIza…",
    models: ["gemini-3.8-flash", "gemini-flash-latest", "gemini-flash-lite-latest"],
    hint: "Безкоштовно: aistudio.google.com/apikey → «Create API key» (потрібен лише Google-акаунт, без картки). Увага: на безкоштовному тарифі Google може використовувати запити для навчання моделей — не обговорюйте з Джарвісом службове й чутливе." },
  groq: { key: "groq_key", model: "groq_model", def: "openai/gpt-oss-120b", ph: "gsk_…",
    models: ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "openai/gpt-oss-20b"],
    hint: "Безкоштовно: console.groq.com/keys → «Create API Key». Є добові ліміти запитів." },
  ollama: { key: null, model: "ollama_model", def: "qwen2.5:7b",
    models: ["qwen2.5:7b", "qwen2.5:14b", "llama3.1:8b"],
    hint: "Повністю офлайн і приватно. Встановіть Ollama з ollama.com, потім у терміналі: ollama pull qwen2.5:7b. Потрібна відеокарта з 8+ ГБ пам'яті, і українською відповідає слабше." },
  anthropic: { key: "api_key", model: "model", def: "claude-sonnet-5-5", ph: "sk-ant-…",
    models: ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"],
    hint: "Платно, ≈1–3 центи за відповідь: console.anthropic.com → API Keys (підписка claude.ai сюди не входить)." },
};
let SETTINGS_RAW = {};
function renderProvider() {
  const p = PROVIDERS[$("#s-provider").value], s = SETTINGS_RAW;
  $("#s-key-label").style.display = p.key ? "" : "none";
  $("#s-key-title").textContent = { openai_key: "Ключ OpenAI API", gemini_key: "Ключ Google Gemini", groq_key: "Ключ Groq", api_key: "Ключ Anthropic API" }[p.key] || "";
  $("#s-key").value = "";
  $("#s-key").placeholder = p.key && s[p.key + "_set"] ? `Збережено: ${s[p.key]}` : (p.ph || "");
  $("#s-key-hint").textContent = p.hint;
  $("#s-models").innerHTML = p.models.map((m) => `<option value="${m}">`).join("");
  $("#s-model").value = s[p.model] || (p.model === "model" ? s.model : "") || p.def;
}
function providerValues() {
  const p = PROVIDERS[$("#s-provider").value], out = {};
  if (p.key && $("#s-key").value.trim()) out[p.key] = $("#s-key").value.trim();
  out[p.model] = $("#s-model").value.trim() || p.def;
  return out;
}
$("#s-provider").addEventListener("change", renderProvider);
async function openSettings() {
  const s = await api("get_settings") || {};
  SETTINGS_RAW = s;
  $("#s-provider").value = s.provider || "gemini";
  renderProvider();
  $("#s-voice").value = s.voice || "uk-UA-OstapNeural";
  $("#s-title").value = s.user_title || "";
  $("#s-name").value = s.user_name || "";
  $("#s-city").value = s.city || "";
  $("#s-wake").checked = !!s.wake_word_enabled;
  $("#s-speak").checked = !!s.speak_responses;
  $("#s-always").checked = (s.listen_mode || "always") === "always";
  $("#s-web").checked = !!s.web_search;
  $("#s-brief").checked = !!s.morning_briefing;
  $("#s-alerts").checked = !!s.proactive_alerts;
  $("#s-welcome").checked = !!s.welcome_back;
  $("#s-sfx").checked = !!s.sfx;
  $("#s-vfx").checked = !!s.voice_fx;
  $("#s-auto").checked = !!s.autostart;
  $("#s-upd").checked = s.auto_update !== false;
  $("#s-obs").value = "";
  $("#s-obs").placeholder = s.obs_password_set ? `Збережено: ${s.obs_password}` : "OBS → Інструменти → Налаштування WebSocket-сервера";
  $("#s-duck").checked = !!s.duck_music;
  $("#s-engine").value = s.wake_engine || "google";
  $("#s-music").value = s.music_service || "ytmusic";
  $("#s-budget").value = s.daily_budget ?? 1;
  $("#s-smini").checked = !!s.start_mini;
  $("#s-ics").value = "";
  $("#s-ics").placeholder = s.calendar_ics ? "Підключено ✓ (введіть нову адресу, щоб замінити)" : "https://calendar.google.com/calendar/ical/…/basic.ics";
  renderMemory(s.memory || []);
  renderPhrases(await api("phrases_list") || []);
  $("#settings").classList.remove("hidden");
}
$("#btn-settings").onclick = openSettings;
$("#s-log").onclick = () => api("open_log");
$("#s-ytm").onclick = () => { api("ytm_login"); $("#settings").classList.add("hidden"); };
$("#s-cancel").onclick = () => $("#settings").classList.add("hidden");
$("#s-reset").onclick = async () => { await api("reset_memory"); HUD.addMessage("jarvis", "Пам'ять розмови очищено, сер."); };
$("#s-save").onclick = async () => {
  await api("save_settings", {
    provider: $("#s-provider").value, ...providerValues(), voice: $("#s-voice").value,
    user_title: $("#s-title").value.trim() || "сер", user_name: $("#s-name").value.trim(), city: $("#s-city").value.trim() || "Київ",
    wake_word_enabled: $("#s-wake").checked, listen_mode: $("#s-always").checked ? "always" : "wake", speak_responses: $("#s-speak").checked, web_search: $("#s-web").checked,
    morning_briefing: $("#s-brief").checked, proactive_alerts: $("#s-alerts").checked, welcome_back: $("#s-welcome").checked,
    sfx: $("#s-sfx").checked, voice_fx: $("#s-vfx").checked,
    autostart: $("#s-auto").checked, auto_update: $("#s-upd").checked,
    ...($("#s-obs").value.trim() ? { obs_password: $("#s-obs").value.trim() } : {}), duck_music: $("#s-duck").checked, start_mini: $("#s-smini").checked, wake_engine: $("#s-engine").value, music_service: $("#s-music").value,
    daily_budget: Math.max(0, parseFloat($("#s-budget").value) || 0),
    ...($("#s-ics").value.trim() ? { calendar_ics: $("#s-ics").value.trim() } : {}),
  });
  SETTINGS = (await api("get_settings")) || SETTINGS;
  $("#settings").classList.add("hidden");
  HUD.addMessage("jarvis", "Налаштування збережено.");
};

/* ---------- ЗАВАНТАЖЕННЯ ---------- */
const BOOT = [
  "STARK INDUSTRIES // MARK OS v7.6",
  "Ініціалізація нейроядра ............ <b>OK</b>",
  "Підключення до Claude AI ........... <b>OK</b>",
  "Калібрування аудіосенсорів ......... <b>OK</b>",
  "Голосовий модуль [uk-UA] ........... <b>OK</b>",
  "Протоколи керування системою ....... <b>OK</b>",
  "Браузерний модуль / YouTube Music ... <b>OK</b>",
  "Діагностика апаратного забезпечення  <b>OK</b>",
  "Усі системи в нормі. Ласкаво просимо.",
];
async function boot() {
  sfx("boot");
  const log = $("#boot-log");
  for (const line of BOOT) {
    log.innerHTML += line + "\n";
    await new Promise((r) => setTimeout(r, 300));
  }
  await new Promise((r) => setTimeout(r, 400));
  $("#boot").classList.add("done");
}

let started = false;
async function start() {
  if (started) return;
  started = true;
  boot();
  resize();
  HUD.setState("idle");
  const info = await api("ready");
  applyInfo(info);
  poll();
}
if (hasPy()) start();
window.addEventListener("pywebviewready", async () => {
  if (!started) return start();
  const info = await api("ready");             // запізнілий міст — переходимо з демо на справжній
  applyInfo(info);
});
// у звичайному браузері (без pywebview) — демо-режим для перегляду
setTimeout(() => { if (!hasPy()) start(); }, 2500);

/* ---------- ГОЛОГРАФІЧНІ КАРТКИ ---------- */
const HOLO_TITLES = {
  weather: "ATMOSPHERIC DATA", system: "SYSTEM DIAGNOSTIC", vision: "VISUAL ANALYSIS", protocol: "PROTOCOL",
  music: "AUDIO SYSTEMS", reminder: "TIMER SET", files: "FILE SEARCH", launch: "INITIATING", intel: "INTEL // WEB SEARCH",
  welcome: "WELCOME HOME", briefing: "MORNING BRIEFING", memory: "MEMORY UPDATED", calendar: "SCHEDULE",
};
const WD = ["НД", "ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ"];

function wxIcon(code, size = 54) {
  const sun = `<circle cx="32" cy="28" r="10" fill="none" stroke="currentColor" stroke-width="3"/>` +
    [...Array(8)].map((_, i) => { const a = i * Math.PI / 4; return `<line x1="${32 + Math.cos(a) * 15}" y1="${28 + Math.sin(a) * 15}" x2="${32 + Math.cos(a) * 20}" y2="${28 + Math.sin(a) * 20}" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>`; }).join("");
  const cloud = `<path d="M18 46h28a10 10 0 0 0 0-20 14 14 0 0 0-26 4 8 8 0 0 0-2 16z" fill="rgba(63,240,255,.12)" stroke="currentColor" stroke-width="3" stroke-linejoin="round"/>`;
  const rain = [20, 30, 40].map((x) => `<line x1="${x}" y1="52" x2="${x - 4}" y2="60" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>`).join("");
  const snow = [20, 32, 44].map((x) => `<circle cx="${x}" cy="56" r="2.5" fill="currentColor"/>`).join("");
  const bolt = `<path d="M34 46l-6 10h7l-4 8" fill="none" stroke="#ffb340" stroke-width="3" stroke-linejoin="round"/>`;
  let g;
  if (code <= 1) g = sun;
  else if (code === 2) g = `<g transform="translate(8,-6) scale(.8)">${sun}</g>` + cloud;
  else if (code <= 48) g = cloud;
  else if (code >= 95) g = cloud + bolt;
  else if ((code >= 71 && code <= 77) || code === 85 || code === 86) g = cloud + snow;
  else g = cloud + rain;
  return `<svg class="wx" viewBox="0 0 64 64" width="${size}" height="${size}">${g}</svg>`;
}

function bar(label, pct, extra = "") {
  const cls = pct >= 90 ? "crit" : pct >= 75 ? "warn" : "";
  return `<div class="hbar ${cls}"><span>${label}</span><i><b style="width:${pct}%"></b></i><em>${extra || Math.round(pct) + "%"}</em></div>`;
}

function holoBody(kind, d) {
  switch (kind) {
    case "weather":
      return `<div class="wx-now">${wxIcon(d.code, 64)}<div><div class="big">${d.temp}°</div><div class="sub">${escapeHtml(d.city || "")} · ${escapeHtml(d.desc || "")}</div>
        <div class="sub"><span class="nw">відчувається ${d.feels}°</span> · <span class="nw">вітер ${d.wind} м/с</span> · <span class="nw">${d.hum}%</span></div></div></div>` +
        (d.daily && d.daily.length > 1 ? `<div class="wx-days">${d.daily.slice(0, 5).map((x) =>
          `<div>${WD[new Date(x.date).getDay()]}${wxIcon(x.code, 28)}<b>${x.max}°</b><span>${x.min}°</span></div>`).join("")}</div>` : "");
    case "system":
      return bar("CPU", d.cpu) + bar("RAM", d.ram) + (d.disks || []).slice(0, 3).map((x) => bar(x.name, x.pct, x.free + " ГБ")).join("") +
        (d.battery != null ? bar("BAT", d.battery) : "") +
        (d.top ? `<div class="list">${d.top.slice(0, 3).map((t) => `<div><span>${escapeHtml(t[0])}</span><b>${t[1]} МБ</b></div>`).join("")}</div>` : "");
    case "vision":
      return `<div class="vision"><img src="data:image/jpeg;base64,${d.img}"><i class="scan"></i><u></u><u></u><u></u><u></u></div><div class="sub">СКАНУВАННЯ ЕКРАНА…</div>`;
    case "protocol":
      return `<div class="big proto">${escapeHtml((d.name || "").toUpperCase())}</div><div class="steps">${(d.steps || []).map((st, i) =>
        `<div class="${i < d.done ? "done" : i === d.done ? "run" : ""}"><b>${i < d.done ? "✓" : i === d.done ? "▸" : "·"}</b>${escapeHtml(st)}</div>`).join("")}</div>
        <div class="sub">${d.done >= (d.steps || []).length ? "ПРОТОКОЛ ВИКОНАНО" : "ВИКОНАННЯ…"}</div>`;
    case "music":
      return `<div class="eq">${"<i></i>".repeat(14)}</div><div class="mid">${escapeHtml(d.title || "")}</div><div class="sub">${escapeHtml(d.service || "YOUTUBE")} · ВІДТВОРЕННЯ</div>`;
    case "reminder":
      return `<div class="big">${d.time}</div><div class="mid">${escapeHtml(d.text || "")}</div><div class="sub">${d.date || ""}</div>`;
    case "files":
      return `<div class="list files">${(d.items || []).map((p) => `<div title="${escapeHtml(p)}"><span>${escapeHtml(p.split(/[\\/]/).pop())}</span></div>`).join("")}</div>`;
    case "launch":
      return `<div class="big launch">${escapeHtml(d.title || "")}</div><div class="sub">${escapeHtml(d.sub || "")}</div><div class="progress"><b></b></div>`;
    case "intel":
      return `<div class="globe"></div><div class="mid">${escapeHtml(d.query || "")}</div><div class="sub">ПОШУК У ГЛОБАЛЬНІЙ МЕРЕЖІ…</div>`;
    case "welcome":
      return `<div class="big">${escapeHtml((d.title || "сер").toUpperCase())}</div><div class="sub">ВІДСУТНІСТЬ: ${d.away} ХВ</div>`;
    case "memory":
      return `<div class="mem-icon"><i></i><i></i><i></i></div><div class="mid">${escapeHtml(d.text || "")}</div><div class="sub">ЗАПИСАНО В ДОВГОТРИВАЛУ ПАМ'ЯТЬ</div>`;
    case "calendar": {
      const ev = d.events || [];
      if (!ev.length) return `<div class="mid">Подій немає</div><div class="sub">${d.date.split("-").reverse().join(".")} · ВІЛЬНИЙ ДЕНЬ</div>`;
      return `<div class="cal">${ev.map((e) => `<div><b>${e.all_day ? "ДЕНЬ" : e.start.slice(11, 16)}</b><span>${escapeHtml(e.title)}${
        d.days > 1 ? ` <em>${e.start.slice(8, 10)}.${e.start.slice(5, 7)}</em>` : ""}</span></div>`).join("")}</div>`;
    }
    case "briefing":
      return `<div class="big">${d.date}</div><div class="sub">ЗБИРАЮ ДАНІ: ПОГОДА · НАГАДУВАННЯ · НОВИНИ</div><div class="progress"><b></b></div>`;
  }
  return "";
}

const HOLO_TTL = { vision: 16000, protocol: 14000, weather: 18000, system: 18000, files: 18000 };
function showHolo(kind, data) {
  const box = $("#holo");
  let card = box.querySelector(`[data-kind="${kind}"]`);
  const fresh = !card;
  if (fresh) {
    card = document.createElement("div");
    card.className = "holo-card";
    card.dataset.kind = kind;
    box.prepend(card);
    while (box.children.length > 3) box.lastChild.remove();
    sfx("card");
  } else if (kind === "protocol") sfx("step");
  card.innerHTML = `<header><span>◢ ${HOLO_TITLES[kind] || kind.toUpperCase()}</span><em>${new Date().toLocaleTimeString("uk-UA")}</em></header>${holoBody(kind, data)}`;
  card.onclick = () => dismiss(card);
  clearTimeout(card._t);
  card._t = setTimeout(() => dismiss(card), HOLO_TTL[kind] || 11000);
}
function dismiss(card) {
  card.classList.add("out");
  setTimeout(() => card.remove(), 450);
}

/* ---------- ПРОТОКОЛИ, МІНІ-РЕЖИМ, КЛАВІШІ ---------- */
function renderProtocols(list) {
  const box = $("#protocols");
  if (!box || !list) return;
  box.innerHTML = list.map((p) => `<button title="${escapeHtml(p.description || "")}" data-name="${escapeHtml(p.name)}">${escapeHtml(p.name.toUpperCase())}</button>`).join("");
  box.querySelectorAll("button").forEach((b) => (b.onclick = () => { sfx("click"); api("run_protocol", b.dataset.name); }));
}
function renderPhrases(list) {
  const box = $("#s-phrases");
  box.innerHTML = list.length ? list.map((p) =>
    `<div><span><b>«${escapeHtml(p.trigger)}»</b> → ${p.responses.map((r) => "«" + escapeHtml(r) + "»").join(" / ")}</span>` +
    `<button data-t="${escapeHtml(p.trigger)}" title="Забути">✕</button></div>`).join("")
    : '<div class="empty">Ще немає — додайте першу</div>';
  box.querySelectorAll("button").forEach((b) => (b.onclick = async () => {
    await api("phrase_delete", b.dataset.t); renderPhrases(await api("phrases_list") || []); }));
}
$("#s-ph-add").onclick = async () => {
  const t = $("#s-ph-trigger").value.trim(), r = $("#s-ph-response").value.trim();
  if (!t || !r) return;
  await api("phrase_add", t, r);
  $("#s-ph-trigger").value = ""; $("#s-ph-response").value = "";
  renderPhrases(await api("phrases_list") || []);
};
function renderMemory(list) {
  const box = $("#s-memory");
  box.innerHTML = list.length ? list.slice().reverse().map((f) =>
    `<div><span>${escapeHtml(f.text)}</span><button data-t="${escapeHtml(f.text)}" title="Забути">✕</button></div>`).join("")
    : '<div class="empty">Поки що нічого</div>';
  box.querySelectorAll("button").forEach((b) => (b.onclick = async () => { await api("forget_fact", b.dataset.t); b.parentElement.remove(); }));
}
function applyInfo(info) {
  if (!info) return;
  if (info.mini) toggleMini(true);
  $("#ver").textContent = "v" + info.version;
  if (info.settings) SETTINGS = info.settings;
  renderProtocols(info.protocols);
}

let mini = false;
async function toggleMini(force) {
  mini = force != null ? force : !mini;
  document.body.classList.toggle("mini", mini);
  await api("set_mini", mini);
  setTimeout(resize, 150);
}
$("#btn-mini").onclick = () => toggleMini();
cv.addEventListener("dblclick", () => toggleMini());
cv.addEventListener("click", () => { if (state === "speaking") api("stop_speaking"); });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (!$("#settings").classList.contains("hidden")) $("#settings").classList.add("hidden");
    else api("stop_speaking");
  }
});

/* ---------- ДЕМО (лише для перегляду в звичайному браузері) ---------- */
const DEMO = {
  ready: () => { setTimeout(() => { HUD.setState("listening");
    HUD.addMessage("user", "Джарвіс, запусти протокол вечірка");
    setTimeout(() => { HUD.setState("thinking"); HUD.toolUsed("run_protocol");
      HUD.holo("protocol", { name: "вечірка", steps: ["ГУЧНІСТЬ 80%", "МУЗИКА: AC/DC BACK IN BLACK"], done: 1 }); }, 300);
    setTimeout(() => { HUD.holo("weather", { city: "Київ", temp: 12, feels: 10, desc: "мінлива хмарність", code: 2, wind: 4, hum: 71,
      daily: [{ date: "2026-10-11", min: 7, max: 13, code: 2 }, { date: "2026-10-12", min: 6, max: 11, code: 61 }, { date: "2026-10-13", min: 5, max: 12, code: 3 },
              { date: "2026-10-14", min: 4, max: 14, code: 0 }, { date: "2026-10-15", min: 6, max: 13, code: 80 }] }); }, 700);
    setTimeout(() => { HUD.addMessage("jarvis", "Протокол «вечірка» активовано, сер. Сусіди, підозрюю, не в захваті."); HUD.setState("listening"); }, 1100);
    }, 2600); return { version: "7.7.0-demo", settings: { sfx: false, voice_fx: true },
      protocols: [{ name: "вечірка" }, { name: "тиша" }, { name: "робота" }, { name: "діагностика" }] }; },
  stats: () => ({ cpu: 20 + Math.random() * 25, cores: Array.from({ length: 16 }, () => Math.random() * 70), ram: 58, ram_used: 18.6, ram_total: 32,
    disk: 71, battery: null, down: Math.random() * 900, up: Math.random() * 200, procs: 287, uptime: 93210,
    usage: { cost: 0.0421, requests: 14, month: 1.37, cached_pct: 81 },
    reminders: [{ when: "2026-10-11T18:30:00", text: "Зателефонувати татові" }] }),
  get_settings: () => ({ provider: "gemini", model: "claude-sonnet-5-5", user_title: "сер", city: "Київ", wake_word_enabled: true, speak_responses: true, web_search: true,
    morning_briefing: true, proactive_alerts: true, welcome_back: true, sfx: true, voice_fx: true, start_mini: true,
    calendar_ics: "x", memory: [{ text: "П'є каву без цукру" }, { text: "Тата звати Олег" }] }),
  toggle_mute: () => (muted = !muted),
  phrases_list: () => [{ trigger: "хто у нас дракон", responses: ["Звісно ж ви, сер. Вогнедишний і непереможний."] }],
  phrase_add: () => "ok", phrase_delete: () => "ok",
  set_mini: (on) => on,
};
