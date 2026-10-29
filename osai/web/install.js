import { Avatar } from "./avatar.js";

const $ = s => document.querySelector(s);
const TOKEN = new URLSearchParams(location.search).get("token") || "";
const DRY = new URLSearchParams(location.search).get("dry") === "1"; // rehearse without touching any disk
const headers = { "content-type": "application/json", "x-osai-token": TOKEN };
const ORDER = ["welcome", "disk", "you", "options", "confirm", "progress"];
const GUIDE = {
  welcome: "Let's put OSAI on this computer for good.",
  disk: "Which disk should I use? I'll erase it completely.",
  you: "Tell me who you are so I can set up your account.",
  options: "Just a few choices about how I start up.",
  confirm: "Last check before I erase the disk.",
  progress: "Copying myself over. This takes a little while.",
};

const avatar = new Avatar($("#guide-avatar"));
let info = { disks: [] };
let disk = null;

function esc(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

function show(step) {
  for (const el of document.querySelectorAll(".step")) el.hidden = el.dataset.step !== step;
  const i = ORDER.indexOf(step);
  for (const li of document.querySelectorAll("#steps li")) {
    const j = ORDER.indexOf(li.dataset.step);
    li.classList.toggle("current", j === i);
    li.classList.toggle("done", j < i);
  }
  $("#guide-text").textContent = GUIDE[step];
  avatar.setState(step === "progress" ? "working" : "idle");
  if (step === "confirm") fillSummary();
  document.querySelector(`.step[data-step="${step}"] input, .step[data-step="${step}"] .btn.primary`)?.focus();
}
const current = () => document.querySelector(".step:not([hidden])").dataset.step;

for (const b of document.querySelectorAll("[data-next]")) b.addEventListener("click", () => {
  const step = current();
  if (step === "you" && !checkYou()) return;
  show(ORDER[ORDER.indexOf(step) + 1]);
});
for (const b of document.querySelectorAll("[data-back]")) b.addEventListener("click", () => show(ORDER[ORDER.indexOf(current()) - 1]));

// ---------------------------------------------------------------- disks
function renderDisks() {
  const box = $("#disks");
  box.innerHTML = "";
  $("#no-disks").hidden = info.disks.some(d => d.usable);
  for (const d of info.disks) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "disk";
    b.setAttribute("role", "radio");
    b.setAttribute("aria-checked", "false");
    b.disabled = !d.usable;
    const kind = d.removable || d.transport === "USB" ? "🔌" : d.transport === "NVME" ? "⚡" : "💽";
    b.innerHTML = `<span class="ico">${kind}</span><span><span class="name">${esc(d.model)}</span><br>
      <span class="meta">${esc(d.path)}${d.transport ? " · " + esc(d.transport) : ""}${d.problem ? " · " + esc(d.problem) : ""}</span></span>
      <span class="size">${esc(d.size_label)}</span>`;
    b.onclick = () => {
      disk = d;
      for (const x of box.children) x.setAttribute("aria-checked", String(x === b));
      $("#disk-next").disabled = false;
    };
    box.appendChild(b);
  }
}

// ---------------------------------------------------------------- you
const slug = s => s.toLowerCase().normalize("NFKD").replace(/[^\w\s-]/g, "").trim().split(/\s+/)[0]?.replace(/[^a-z0-9_-]/g, "") || "";
let userEdited = false, hostEdited = false;
$("#username").addEventListener("input", () => { userEdited = true; if (!hostEdited) $("#hostname").value = $("#username").value ? `${$("#username").value}-osai` : ""; });
$("#hostname").addEventListener("input", () => { hostEdited = true; });
$("#fullname").addEventListener("input", () => {
  if (!userEdited) $("#username").value = slug($("#fullname").value).slice(0, 32);
  if (!hostEdited) $("#hostname").value = $("#username").value ? `${$("#username").value}-osai` : "";
});

function checkYou() {
  const err = msg => { $("#you-error").textContent = msg; $("#you-error").hidden = !msg; return !msg; };
  const u = $("#username").value;
  if (!/^[a-z_][a-z0-9_-]{0,31}$/.test(u) || ["root", "osai"].includes(u)) return err("Choose a username of lowercase letters and digits, like \"ana\".");
  if (!/^[a-zA-Z0-9][a-zA-Z0-9-]{0,62}$/.test($("#hostname").value)) return err("The computer name may only use letters, digits and dashes.");
  if ($("#password").value.length < 4) return err("Choose a password of at least 4 characters.");
  if ($("#password").value !== $("#password2").value) return err("The two passwords don't match.");
  return err("");
}

// ---------------------------------------------------------------- confirm
function request() {
  return {
    disk: disk?.path, confirm: $("#confirm").value.trim(),
    fullname: $("#fullname").value.trim(), username: $("#username").value, hostname: $("#hostname").value,
    timezone: $("#timezone").value, password: $("#password").value, password2: $("#password2").value,
    autologin: $("#autologin").checked, copy_settings: $("#copy_settings").checked,
    passwordless_admin: $("#passwordless_admin").checked, dry_run: DRY,
  };
}

function fillSummary() {
  const r = request();
  const rows = [["Disk", `${disk.model} · ${disk.path} · ${disk.size_label} — <strong>will be erased</strong>`],
    ["Account", `${esc(r.fullname || r.username)} (${esc(r.username)})`], ["Computer name", esc(r.hostname)], ["Time zone", esc(r.timezone)],
    ["Start-up", r.autologin ? "Opens OSAI without a password" : "Asks for your password"],
    ["Settings", r.copy_settings ? "Brought over from this session" : "Fresh start"],
    ["Admin tasks", r.passwordless_admin ? "OSAI may run them without your password" : "Need your password"]];
  $("#summary").innerHTML = rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("");
  $("#confirm-disk").textContent = `${disk.model} (${disk.path})`;
  $("#confirm-name").textContent = disk.name;
  $("#confirm").value = "";
  $("#go").disabled = true;
}
$("#confirm").addEventListener("input", () => { $("#go").disabled = $("#confirm").value.trim() !== disk?.name; });

$("#go").addEventListener("click", async () => {
  $("#go").disabled = true;
  const res = await fetch("/api/install", { method: "POST", headers, body: JSON.stringify(request()) });
  if (!res.ok) {
    $("#confirm-error").textContent = (await res.json().catch(() => ({}))).detail || res.statusText;
    $("#confirm-error").hidden = false;
    $("#go").disabled = false;
    return;
  }
  $("#password").value = $("#password2").value = "";
  show("progress");
});

// ---------------------------------------------------------------- progress
function progress(ev) {
  if (!ev || ev.state === "idle" || (ev.state === "failed" && current() !== "progress" && current() !== "confirm")) return;
  if (current() !== "progress") show("progress");
  $("#bar-fill").style.width = `${ev.percent}%`;
  $("#progress-pct").textContent = ev.percent;
  $("#progress-msg").textContent = ev.state === "failed" ? "Stopped" : ev.message;
  const done = ev.state === "done", failed = ev.state === "failed";
  $("#progress-title").textContent = done ? (DRY ? "Rehearsal finished" : "OSAI is installed") : failed ? "The installation stopped" : "Installing OSAI…";
  $("#progress-note").textContent = done ? (DRY ? "Nothing was changed — this was a rehearsal." : "Restart, and remove the USB stick when the screen goes dark.")
    : failed ? "Your computer still starts from the USB as before. The disk you picked may be partly erased." : "Keep the USB plugged in until it's done.";
  $("#progress-error").hidden = !failed;
  $("#progress-error").textContent = failed ? ev.message : "";
  $("#done-actions").hidden = !done;
  $("#fail-actions").hidden = !failed;
  $("#reboot").hidden = DRY;
  avatar.setState(done ? "happy" : failed ? "error" : "working");
  $("#guide-text").textContent = done ? "All done! See you on the other side." : failed ? "Something went wrong. Here's what I know." : GUIDE.progress;
  if (done) for (const li of document.querySelectorAll("#steps li")) { li.classList.add("done"); li.classList.remove("current"); }
}

$("#reboot").addEventListener("click", () => fetch("/api/install/reboot", { method: "POST", headers }));
$("#retry").addEventListener("click", () => show(disk ? "confirm" : "disk"));

function connect() {
  const q = TOKEN ? "?token=" + encodeURIComponent(TOKEN) : "";
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws${q}`);
  ws.onmessage = e => {
    const ev = JSON.parse(e.data);
    if (ev.type === "install") progress(ev);
    if (ev.type === "hello" || ev.type === "config") avatar.setConfig(ev.config.avatar);
  };
  ws.onclose = () => setTimeout(connect, 1500);
}

(async function boot() {
  info = await (await fetch("/api/install", { headers })).json();
  $("#fw").textContent = info.firmware === "uefi" ? "UEFI" : "BIOS (legacy)";
  $("#not-live").hidden = info.live;
  document.querySelector('.step[data-step="welcome"] [data-next]').disabled = !info.live;
  renderDisks();
  const here = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const zones = ["UTC", ...info.timezones];
  $("#timezone").innerHTML = zones.map(z => `<option${z === here ? " selected" : ""}>${esc(z)}</option>`).join("");
  connect();
  show("welcome");
  // Come back to a running or finished install (e.g. after a reload); a failed one starts over.
  if (info.progress && ["running", "done"].includes(info.progress.state)) progress(info.progress);
})();
