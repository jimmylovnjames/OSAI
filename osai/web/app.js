import { Avatar } from "./avatar.js";
import { Voice } from "./voice.js";

const $ = s => document.querySelector(s);
const params = new URLSearchParams(location.search);
const TOKEN = params.get("token") || "";
const MINI = params.get("mini") === "1";
if (MINI) document.body.classList.add("mini");

const FORMS = ["orb", "face", "bot", "cat", "ghost"];
const EYES = ["round", "visor", "sleepy", "star", "dot"];
const ACCESSORIES = ["none", "antenna", "halo", "ears", "headphones", "crown"];
const WALLPAPERS = ["aurora", "nebula", "sunset", "ocean", "mono", "matrix"];

let config = {};
let serverCaps = {};
let ws = null;
let replyText = "";
let replyEl = null;
let thinkingEl = null;
const toolEls = new Map();

const avatar = new Avatar($("#avatar"));
const preview = MINI ? null : new Avatar($("#avatar-preview"));

// ------------------------------------------------------------------ helpers
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Small, safe Markdown renderer: everything is escaped first, then a few constructs are re-enabled.
function md(src) {
  const blocks = [];
  let s = esc(src).replace(/```(\w*)\n?([\s\S]*?)```/g, (_, l, code) => { blocks.push(`<pre><code>${code}</code></pre>`); return `\u0000${blocks.length - 1}\u0000`; });
  const inline = t => t
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  const lines = s.split("\n"), out = [];
  let list = null, table = null;
  const close = () => { if (list) { out.push(`</${list}>`); list = null; } if (table) { out.push("</table>"); table = null; } };
  for (const line of lines) {
    let m;
    if (/^\s*\|.*\|\s*$/.test(line)) {
      if (/^\s*\|[\s:|-]+\|\s*$/.test(line)) continue;
      if (!table) { close(); out.push("<table>"); table = true; }
      out.push("<tr>" + line.trim().slice(1, -1).split("|").map(c => `<td>${inline(c.trim())}</td>`).join("") + "</tr>");
    } else if ((m = line.match(/^\s*[-*•]\s+(.*)/))) {
      if (list !== "ul") { close(); out.push("<ul>"); list = "ul"; }
      out.push(`<li>${inline(m[1])}</li>`);
    } else if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) {
      if (list !== "ol") { close(); out.push("<ol>"); list = "ol"; }
      out.push(`<li>${inline(m[1])}</li>`);
    } else if ((m = line.match(/^(#{1,4})\s+(.*)/))) {
      close(); out.push(`<h${m[1].length + 2}>${inline(m[2])}</h${m[1].length + 2}>`);
    } else if (line.trim() === "") {
      close();
    } else {
      close(); out.push(`<p>${inline(line)}</p>`);
    }
  }
  close();
  return out.join("").replace(/\u0000(\d+)\u0000/g, (_, i) => blocks[i]);
}

function get(obj, path) { return path.split(".").reduce((o, k) => (o ?? {})[k], obj); }
function nest(path, value) { return path.split(".").reverse().reduce((acc, k) => ({ [k]: acc }), value); }

const TOOL_LABELS = {
  run_command: a => `Running \`${(a.command || "").slice(0, 60)}\``, launch_app: a => `Opening ${a.name}`, close_app: a => `Closing ${a.name}`,
  list_apps: () => "Looking at installed apps", list_windows: () => "Checking open windows", window_action: a => `${a.action} ${a.match}`,
  install_app: a => `Installing ${a.package}`, search_packages: a => `Searching software for “${a.query}”`,
  list_directory: a => `Browsing ${a.path}`, read_file: a => `Reading ${a.path}`, write_file: a => `Writing ${a.path}`,
  find_files: a => `Searching files for “${a.pattern}”`, move_path: a => `${a.copy ? "Copying" : "Moving"} ${a.source}`, make_folder: a => `Creating ${a.path}`,
  delete_path: a => `Deleting ${a.path}`, open_path: a => `Opening ${a.target}`,
  system_info: () => "Checking the system", set_volume: () => "Adjusting volume", set_brightness: () => "Adjusting brightness",
  set_dark_mode: a => a.dark ? "Switching to dark mode" : "Switching to light mode", wifi: a => `Wi-Fi: ${a.action}`, bluetooth: a => `Bluetooth: ${a.action}`,
  power: a => `Power: ${a.action}`, media_control: a => `Media: ${a.action}`, clipboard: a => `Clipboard ${a.action}`, os_notification: () => "Sending a notification",
  list_processes: () => "Checking running programs", kill_process: a => `Stopping process ${a.pid}`,
  see_screen: () => "Looking at the screen", mouse: a => `Mouse ${a.action}`, keyboard: a => a.keys ? `Pressing ${a.keys}` : "Typing", wait: a => `Waiting ${a.seconds}s`,
  browser_open: a => `Browsing ${a.url}`, browser_read: () => "Reading the page", browser_click: a => `Clicking [${a.element}]`,
  browser_type: () => "Typing into the page", browser_action: a => `Browser: ${a.action}`,
  web_search: () => "Searching the web", web_fetch: () => "Fetching a page",
  customize_avatar: () => "Changing my look", customize_screen: () => "Changing the screen", show_card: a => `Showing “${a.title}”`,
  remember: () => "Remembering that", forget: () => "Forgetting", schedule: a => `Scheduling “${a.label}”`, list_schedule: () => "Checking the schedule",
  cancel_schedule: () => "Cancelling", osai_settings: () => "Updating my settings",
};
const toolLabel = (name, input) => { try { return (TOOL_LABELS[name] || (() => name.replace(/_/g, " ")))(input || {}); } catch { return name; } };

// ------------------------------------------------------------------ UI pieces
const feed = $("#feed-list");
function feedItem(cls, html) {
  const li = document.createElement("li");
  li.className = cls;
  li.innerHTML = html;
  feed.appendChild(li);
  while (feed.children.length > 150) feed.firstChild.remove();
  feed.scrollTop = feed.scrollHeight;
  return li;
}

function toast(text, alarm = false) {
  const t = document.createElement("div");
  t.className = "toast glass" + (alarm ? " alarm" : "");
  t.textContent = text;
  $("#toast").appendChild(t);
  setTimeout(() => t.remove(), alarm ? 15000 : 5000);
}

function showCard({ title, body, image_url }) {
  const wrap = $("#cards");
  for (const c of wrap.querySelectorAll(".card")) if (c.dataset.title === title) c.remove();
  const card = document.createElement("article");
  card.className = "card glass";
  card.dataset.title = title;
  const img = image_url && /^https?:\/\//.test(image_url) ? `<img src="${esc(image_url)}" alt="">` : "";
  card.innerHTML = `<button class="close" aria-label="Close">✕</button><h3>${esc(title)}</h3><div class="md">${md(body)}</div>${img}`;
  card.querySelector(".close").onclick = () => card.remove();
  wrap.prepend(card);
  while (wrap.children.length > 4) wrap.lastChild.remove();
}

function showApproval({ id, tool, input, reason }) {
  if (document.getElementById("ap-" + id)) return;
  const box = document.createElement("div");
  box.className = "approval glass";
  box.id = "ap-" + id;
  const detail = tool === "run_command" ? input.command : JSON.stringify(input, null, 1);
  box.innerHTML = `<h4>Needs your OK</h4><p>${esc(reason)}</p><code>${esc(detail)}</code>
    <div class="actions"><button class="btn deny">Don't</button><button class="btn allow">Allow</button></div>`;
  box.querySelector(".allow").onclick = () => send({ type: "approve", id, allow: true });
  box.querySelector(".deny").onclick = () => send({ type: "approve", id, allow: false });
  $("#approvals").appendChild(box);
  voice.replyDone = true;
  voice.say("I need your OK for this. Say yes or no.");
}

function setState(state) {
  const voiceState = voice.listening ? "listening" : voice.speaking ? "speaking" : null;
  avatar.setState(voiceState || state);
  const dot = $("#status-dot");
  dot.className = "dot " + (state === "idle" ? "on" : "busy");
  $("#btn-stop").hidden = state === "idle";
}

let agentState = "idle";

// ------------------------------------------------------------------ voice
const voice = new Voice({
  getConfig: () => config,
  serverCaps,
  token: TOKEN,
  onTranscript: (text, handsFree) => {
    if (!text) { $("#caption-user").textContent = "Yes?"; voice.followUpUntil = Date.now() + 8000; return; }
    send({ type: "say", text, source: "spoken" });
  },
  onInterim: text => { $("#caption-user").textContent = text; },
  onListening: (on, passive) => {
    $("#btn-mic").classList.toggle("live", on && !passive);
    avatar.setState(on && !passive ? "listening" : voice.speaking ? "speaking" : agentState);
    if (on && !passive) $("#caption-user").textContent = "Listening…";
  },
  onSpeaking: on => avatar.setState(on ? "speaking" : agentState),
  onAmplitude: a => { avatar.amplitude = a; },
});

// ------------------------------------------------------------------ socket
function send(msg) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(msg)); }

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const q = new URLSearchParams({ ...(TOKEN ? { token: TOKEN } : {}), ...(MINI ? { mini: "1" } : {}) });
  ws = new WebSocket(`${proto}://${location.host}/ws?${q}`);
  ws.onopen = () => { $("#status-dot").className = "dot on"; };
  ws.onclose = () => { $("#status-dot").className = "dot off"; avatar.setState("idle"); setTimeout(connect, 1500); };
  ws.onmessage = e => handle(JSON.parse(e.data));
}

function handle(ev) {
  switch (ev.type) {
    case "hello":
      applyConfig(ev.config);
      if (!feed.children.length) for (const r of ev.recent || []) if (r.type !== "approval_request" && r.type !== "approval_closed") handle({ ...r, replay: true });
      for (const r of ev.recent || []) if (r.type === "approval_request" && !(ev.recent || []).some(x => x.type === "approval_closed" && x.id === r.id)) showApproval(r);
      agentState = ev.busy ? "thinking" : "idle"; setState(agentState);
      break;
    case "config": applyConfig(ev.config); break;
    case "state": agentState = ev.state; setState(ev.state); break;
    case "user":
      $("#caption-user").textContent = ev.text;
      $("#caption-ai").textContent = ""; $("#caption-ai").classList.remove("thinking");
      feedItem("user", esc(ev.text));
      replyText = ""; replyEl = null; thinkingEl = null;
      break;
    case "thinking":
      if (!thinkingEl) thinkingEl = feedItem("think", "");
      thinkingEl.textContent = (thinkingEl.textContent + ev.text).slice(-400);
      if (!replyText) { $("#caption-ai").classList.add("thinking"); $("#caption-ai").textContent = thinkingEl.textContent.split(/(?<=[.!?])\s/).pop().slice(-140); }
      break;
    case "assistant_delta":
      $("#caption-ai").classList.remove("thinking");
      replyText += ev.text;
      $("#caption-ai").textContent = replyText;
      if (!replyEl) replyEl = feedItem("ai", "");
      replyEl.textContent = replyText;
      voice.feed(ev.text);
      thinkingEl = null;
      break;
    case "assistant_done":
      if (ev.replay) { feedItem("ai", esc(ev.text)); break; }
      voice.flush();
      if (!replyText && ev.text) { $("#caption-ai").textContent = ev.text; feedItem("ai", esc(ev.text)); }
      if (ev.interim) { replyText = ""; replyEl = null; }
      if (!ev.interim && !ev.cancelled) { avatar.setState("happy"); setTimeout(() => avatar.state === "happy" && avatar.setState(voice.speaking ? "speaking" : "idle"), 900); }
      break;
    case "tool_start": {
      const li = feedItem("tool run", `<span class="st"></span><span>${md(toolLabel(ev.name, ev.input)).replace(/^<p>|<\/p>$/g, "")}<span class="detail"></span></span>`);
      toolEls.set(ev.id, li);
      if (!replyText) { $("#caption-ai").classList.add("thinking"); $("#caption-ai").textContent = toolLabel(ev.name, ev.input).replace(/`/g, "") + "…"; }
      break;
    }
    case "tool_end": {
      const li = toolEls.get(ev.id);
      if (li) { li.classList.remove("run"); li.classList.add(ev.ok ? "ok" : "bad"); if (!ev.ok) li.querySelector(".detail").textContent = ev.summary || ""; }
      break;
    }
    case "approval_request": showApproval(ev); avatar.setState("waiting"); break;
    case "approval_closed": document.getElementById("ap-" + ev.id)?.remove(); break;
    case "card": showCard(ev); break;
    case "notify": toast(ev.text, ev.alarm); if (!ev.replay) feedItem("ai", "🔔 " + esc(ev.text)); break;
    case "notice": toast(ev.text); break;
    case "speak": voice.replyDone = true; voice.say(ev.text); break;
    case "listen": if (!voice.listening) voice.listenOnce(); break;
    case "error":
      feedItem("err", esc(ev.text));
      if (!ev.replay) {
        $("#caption-ai").classList.remove("thinking"); $("#caption-ai").textContent = ev.text;
        avatar.setState("error"); voice.replyDone = true; voice.say(ev.text);
        if (ev.code === "auth") openSettings("brain");
      }
      break;
    case "reset": feed.innerHTML = ""; $("#cards").innerHTML = ""; $("#caption-user").textContent = ""; $("#caption-ai").textContent = "Fresh start. What's next?"; break;
  }
}

// ------------------------------------------------------------------ config & settings
function applyConfig(c) {
  config = c;
  avatar.setConfig(c.avatar);
  preview?.setConfig(c.avatar);
  document.body.dataset.wallpaper = c.theme?.wallpaper || "aurora";
  document.documentElement.style.setProperty("--accent", c.theme?.accent || c.avatar?.primary || "#7c5cff");
  $("#brand-name").textContent = (c.assistant_name || "OSAI").toUpperCase();
  document.title = c.assistant_name || "OSAI";
  $("#input").placeholder = `Ask ${c.assistant_name || "OSAI"} to do anything…`;
  $("#clock").hidden = c.theme?.clock === false;
  startMatrix(c.theme?.wallpaper === "matrix");
  if (!$("#settings").hidden) fillSettings();
  const keyStatus = c.api_key ? `Key saved (…${c.api_key.slice(4)}).` : c.api_key_env ? "Using the key from the environment." : "No key yet - get one at console.anthropic.com.";
  $("#key-status").textContent = keyStatus;
}

function segButtons(container, options, path) {
  container.innerHTML = "";
  for (const o of options) {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = o;
    b.classList.toggle("active", get(config, path) === o);
    b.onclick = () => saveSetting(path, o);
    container.appendChild(b);
  }
}

function fillSettings() {
  for (const s of document.querySelectorAll(".seg[data-k]")) {
    const k = s.dataset.k;
    segButtons(s, k === "avatar.form" ? FORMS : k === "avatar.eyes" ? EYES : k === "avatar.accessory" ? ACCESSORIES : WALLPAPERS, k);
  }
  for (const i of document.querySelectorAll("#settings [data-k]:not(.seg)")) {
    const v = get(config, i.dataset.k);
    if (i.type === "checkbox") i.checked = !!v;
    else if (document.activeElement !== i) i.value = v ?? "";
  }
  const caps = [];
  caps.push(voice.SR ? "Browser speech recognition available." : "No browser speech recognition.");
  caps.push(serverCaps.server_stt ? "On-device Whisper available." : "On-device Whisper not installed.");
  caps.push(serverCaps.server_tts ? `On-device voice: ${serverCaps.server_tts}.` : "");
  $("#voice-caps").textContent = caps.join(" ");
}

async function saveSetting(path, value) {
  const patch = nest(path, value);
  if (path.startsWith("avatar.")) { avatar.setConfig({ ...config.avatar, ...patch.avatar }); preview?.setConfig({ ...config.avatar, ...patch.avatar }); }
  const res = await fetch("/api/config", { method: "POST", headers: { "content-type": "application/json", "x-osai-token": TOKEN }, body: JSON.stringify(patch) });
  if (!res.ok) toast("Couldn't save: " + ((await res.json()).detail || res.statusText));
}

function openSettings(tab) {
  $("#settings").hidden = false;
  fillSettings();
  if (tab) selectTab(tab);
}
function selectTab(name) {
  for (const b of document.querySelectorAll(".tabs button")) b.classList.toggle("active", b.dataset.tab === name);
  for (const t of document.querySelectorAll(".tab")) t.hidden = t.dataset.tab !== name;
}

function populateVoices() {
  const sel = $("#voice-select");
  if (!sel || !("speechSynthesis" in window)) return;
  const voices = speechSynthesis.getVoices();
  sel.innerHTML = `<option value="">System default</option>` + voices.map(v => `<option value="${esc(v.name)}">${esc(v.name)} (${esc(v.lang)})</option>`).join("");
  sel.value = config.voice?.voice_name || "";
}

// ------------------------------------------------------------------ status bar
async function pollStatus() {
  try {
    const s = await (await fetch("/api/status")).json();
    $("#ind-cpu").textContent = `CPU ${Math.round(s.cpu)}%`;
    $("#ind-net").textContent = s.online ? "● Online" : "○ Offline";
    $("#ind-bat").textContent = s.battery ? `${s.battery.plugged ? "⚡" : "▮"} ${s.battery.percent}%` : "";
  } catch {}
}
function tickClock() {
  const d = new Date();
  $("#clock").textContent = d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }) + "  " +
    d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

let matrixTimer = null;
function startMatrix(on) {
  const cv = $("#matrix");
  clearInterval(matrixTimer);
  if (!on || !cv) return;
  const ctx = cv.getContext("2d");
  cv.width = innerWidth; cv.height = innerHeight;
  const cols = Math.floor(cv.width / 16), drops = Array(cols).fill(0).map(() => Math.random() * -50);
  matrixTimer = setInterval(() => {
    ctx.fillStyle = "#0003"; ctx.fillRect(0, 0, cv.width, cv.height);
    ctx.fillStyle = "#3dff7a"; ctx.font = "15px monospace";
    drops.forEach((y, i) => { ctx.fillText(String.fromCharCode(0x30a0 + Math.random() * 96), i * 16, y * 16); drops[i] = y * 16 > cv.height && Math.random() > .975 ? 0 : y + 1; });
  }, 60);
}

// ------------------------------------------------------------------ wiring
$("#ask").addEventListener("submit", e => {
  e.preventDefault();
  const text = $("#input").value.trim();
  if (!text) return;
  voice.stopSpeaking();
  send({ type: "say", text });
  $("#input").value = "";
});
$("#btn-mic").onclick = () => voice.listenOnce();
$("#avatar").onclick = () => voice.listenOnce();
$("#btn-stop").onclick = () => { send({ type: "cancel" }); voice.stopSpeaking(); };
$("#btn-hands").onclick = () => {
  const on = !$("#btn-hands").classList.contains("active");
  $("#btn-hands").classList.toggle("active", on);
  voice.setHandsFree(on);
  toast(on ? `Hands-free on - say “${config.voice?.wake_word || "osai"}” then your request.` : "Hands-free off");
};
$("#btn-feed").onclick = () => $("#feed").classList.toggle(matchMedia("(max-width: 900px)").matches ? "pinned" : "hidden");
$("#btn-reset").onclick = () => send({ type: "reset" });
$("#btn-settings").onclick = () => $("#settings").hidden ? openSettings() : ($("#settings").hidden = true);
$("#btn-close-settings").onclick = () => { $("#settings").hidden = true; };
for (const b of document.querySelectorAll(".tabs button")) b.onclick = () => selectTab(b.dataset.tab);
for (const i of document.querySelectorAll("#settings [data-k]:not(.seg)")) {
  const evt = i.type === "range" || i.type === "color" ? "input" : "change";
  i.addEventListener(evt, () => {
    let v = i.type === "checkbox" ? i.checked : i.type === "range" ? parseFloat(i.value) : i.value;
    if (i.dataset.k === "api_key" && !v) return;
    clearTimeout(i._t);
    i._t = setTimeout(() => saveSetting(i.dataset.k, v), i.type === "range" || i.type === "color" ? 120 : 0);
  });
}
$("#btn-random").onclick = () => {
  const pick = a => a[Math.floor(Math.random() * a.length)];
  const hue = () => "#" + Math.floor(Math.random() * 0xffffff).toString(16).padStart(6, "0");
  const patch = { form: pick(FORMS), eyes: pick(EYES), accessory: pick(ACCESSORIES), primary: hue(), accent: hue() };
  avatar.setConfig({ ...config.avatar, ...patch });
  fetch("/api/config", { method: "POST", headers: { "content-type": "application/json", "x-osai-token": TOKEN }, body: JSON.stringify({ avatar: patch }) });
};
$("#btn-test-voice").onclick = () => { voice.replyDone = true; voice.say(`Hi${config.user_name ? " " + config.user_name : ""}, I'm ${config.assistant_name || "OSAI"}. This is how I sound.`); };

// Push-to-talk: hold Space when not typing.
let spaceDown = false;
addEventListener("keydown", e => {
  if (e.code === "Space" && !e.repeat && !["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) {
    e.preventDefault(); spaceDown = true; if (!voice.listening) voice.listenOnce();
  }
  if (e.key === "Escape") { send({ type: "cancel" }); voice.stopSpeaking(); $("#settings").hidden = true; }
});
addEventListener("keyup", e => { if (e.code === "Space" && spaceDown) { spaceDown = false; setTimeout(() => voice.stopListening(), 250); } });

if ("speechSynthesis" in window) speechSynthesis.onvoiceschanged = populateVoices;

(async function boot() {
  try {
    const r = await (await fetch("/api/config", { headers: { "x-osai-token": TOKEN } })).json();
    Object.assign(serverCaps, r.voice || {});
    applyConfig(r.config);
    populateVoices();
    if (r.config.voice?.hands_free) $("#btn-hands").click();
    $("#caption-ai").textContent = `Hi${r.config.user_name ? " " + r.config.user_name : ""}, I'm ${r.config.assistant_name}. What should we do?`;
  } catch {}
  connect();
  tickClock(); setInterval(tickClock, 1000 * 15);
  pollStatus(); setInterval(pollStatus, 5000);
})();
