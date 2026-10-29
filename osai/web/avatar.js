// OSAI avatar: a parametric, animated SVG character.
// Config: {form, primary, accent, eyes, accessory, mouth, glow, size}
// States: idle | listening | thinking | working | speaking | waiting | error | happy

const NS = "http://www.w3.org/2000/svg";
const el = (tag, attrs = {}, parent) => {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
};

function shade(hex, amt) {
  let h = hex.replace("#", "");
  if (h.length === 3) h = h.split("").map(c => c + c).join("");
  const n = parseInt(h, 16);
  const c = [n >> 16, (n >> 8) & 255, n & 255].map(v => Math.max(0, Math.min(255, Math.round(v + amt * 255))));
  return "#" + c.map(v => v.toString(16).padStart(2, "0")).join("");
}

export class Avatar {
  constructor(host) {
    this.host = host;
    this.state = "idle";
    this.amplitude = 0;      // 0..1 mouth openness target (driven by speech)
    this.mouthOpen = 0;
    this.look = { x: 0, y: 0 };
    this.lookTarget = { x: 0, y: 0 };
    this.blink = 0;
    this.nextBlink = performance.now() + 2500;
    this.t0 = performance.now();
    this.flash = 0;
    this.cfg = null;
    this.svg = el("svg", { viewBox: "0 0 400 400", class: "avatar-svg", role: "img", "aria-label": "OSAI avatar" });
    host.appendChild(this.svg);
    window.addEventListener("pointermove", e => {
      const r = this.svg.getBoundingClientRect();
      const dx = (e.clientX - (r.left + r.width / 2)) / innerWidth;
      const dy = (e.clientY - (r.top + r.height / 2)) / innerHeight;
      this.lookTarget = { x: Math.max(-1, Math.min(1, dx * 2.2)), y: Math.max(-1, Math.min(1, dy * 2.2)) };
      this.lastPointer = performance.now();
    });
    requestAnimationFrame(t => this.frame(t));
  }

  setState(s) {
    if (s === this.state) return;
    this.state = s;
    this.host.dataset.state = s;
    if (s === "error") this.flash = 1;
  }

  setConfig(cfg) {
    const key = JSON.stringify(cfg);
    if (key === this._cfgKey) return;
    this._cfgKey = key;
    this.cfg = { form: "orb", primary: "#7c5cff", accent: "#00e5ff", eyes: "round", accessory: "none",
                 mouth: true, glow: 0.8, size: 1, ...cfg };
    this.build();
  }

  build() {
    const c = this.cfg, svg = this.svg;
    svg.innerHTML = "";
    const defs = el("defs", {}, svg);
    const body = el("radialGradient", { id: "av-body", cx: "35%", cy: "30%", r: "80%" }, defs);
    el("stop", { offset: "0%", "stop-color": shade(c.primary, 0.25) }, body);
    el("stop", { offset: "55%", "stop-color": c.primary }, body);
    el("stop", { offset: "100%", "stop-color": shade(c.accent, -0.15) }, body);
    const glow = el("radialGradient", { id: "av-glow" }, defs);
    el("stop", { offset: "0%", "stop-color": c.accent, "stop-opacity": String(0.55 * c.glow) }, glow);
    el("stop", { offset: "60%", "stop-color": c.primary, "stop-opacity": String(0.18 * c.glow) }, glow);
    el("stop", { offset: "100%", "stop-color": c.primary, "stop-opacity": "0" }, glow);
    const blur = el("filter", { id: "av-soft", x: "-50%", y: "-50%", width: "200%", height: "200%" }, defs);
    el("feGaussianBlur", { stdDeviation: "6" }, blur);
    const shine = el("linearGradient", { id: "av-shine", x1: "0", y1: "0", x2: "0", y2: "1" }, defs);
    el("stop", { offset: "0%", "stop-color": "#fff", "stop-opacity": ".55" }, shine);
    el("stop", { offset: "100%", "stop-color": "#fff", "stop-opacity": "0" }, shine);

    this.halo = el("circle", { cx: 200, cy: 200, r: 190, fill: "url(#av-glow)" }, svg);
    this.rings = el("g", { fill: "none", stroke: c.accent, "stroke-width": 2 }, svg);
    this.ringEls = [0, 1, 2].map(() => el("circle", { cx: 200, cy: 200, r: 120, opacity: 0 }, this.rings));
    this.orbit = el("g", {}, svg);
    this.orbitDots = [0, 1, 2].map(i => el("circle", { r: 6 - i, fill: c.accent, opacity: 0 }, this.orbit));
    this.spinner = el("circle", { cx: 200, cy: 200, r: 150, fill: "none", stroke: c.accent, "stroke-width": 3,
                                  "stroke-dasharray": "18 22", "stroke-linecap": "round", opacity: 0 }, svg);

    this.root = el("g", {}, svg);           // bobbing group
    this.behind = el("g", {}, this.root);   // accessories behind head
    this.head = el("g", {}, this.root);
    this.front = el("g", {}, this.root);
    this.drawBody();
    this.drawAccessory();
    this.face = el("g", {}, this.head);
    this.drawEyes();
    this.mouth = c.mouth ? el("path", { fill: c.form === "bot" ? c.accent : shade(c.primary, -0.45), stroke: "none" }, this.face) : null;
    this.tint = el("circle", { cx: 200, cy: 200, r: 130, fill: "#ff3b5c", opacity: 0, "pointer-events": "none" }, this.root);
  }

  drawBody() {
    const c = this.cfg, h = this.head;
    const fill = "url(#av-body)";
    if (c.form === "orb") {
      el("circle", { cx: 200, cy: 200, r: 118, fill }, h);
      el("ellipse", { cx: 170, cy: 150, rx: 60, ry: 34, fill: "url(#av-shine)", transform: "rotate(-20 170 150)" }, h);
    } else if (c.form === "face") {
      el("ellipse", { cx: 200, cy: 205, rx: 112, ry: 124, fill }, h);
      el("ellipse", { cx: 140, cy: 245, rx: 18, ry: 10, fill: "#ff7aa8", opacity: .35 }, h);
      el("ellipse", { cx: 260, cy: 245, rx: 18, ry: 10, fill: "#ff7aa8", opacity: .35 }, h);
      el("ellipse", { cx: 175, cy: 140, rx: 50, ry: 26, fill: "url(#av-shine)", transform: "rotate(-15 175 140)" }, h);
    } else if (c.form === "bot") {
      el("rect", { x: 85, y: 95, width: 230, height: 210, rx: 48, fill }, h);
      el("rect", { x: 112, y: 135, width: 176, height: 120, rx: 30, fill: shade(c.primary, -0.55), opacity: .85 }, h);
      el("rect", { x: 70, y: 175, width: 18, height: 50, rx: 8, fill: shade(c.accent, -0.1) }, h);
      el("rect", { x: 312, y: 175, width: 18, height: 50, rx: 8, fill: shade(c.accent, -0.1) }, h);
    } else if (c.form === "cat") {
      el("path", { d: "M100 150 L120 55 L185 110 Z", fill: c.primary }, h);
      el("path", { d: "M300 150 L280 55 L215 110 Z", fill: c.primary }, h);
      el("path", { d: "M118 135 L128 80 L165 112 Z", fill: "#ff9cc2", opacity: .6 }, h);
      el("path", { d: "M282 135 L272 80 L235 112 Z", fill: "#ff9cc2", opacity: .6 }, h);
      el("ellipse", { cx: 200, cy: 210, rx: 120, ry: 110, fill }, h);
      for (const s of [-1, 1]) for (const dy of [-8, 8])
        el("line", { x1: 200 + s * 60, y1: 240 + dy / 2, x2: 200 + s * 125, y2: 235 + dy * 1.6, stroke: "#fff", "stroke-width": 2, opacity: .5 }, h);
    } else if (c.form === "ghost") {
      el("path", { d: "M90 210 C90 120 140 80 200 80 C260 80 310 120 310 210 L310 320 Q290 300 272 320 Q254 340 236 320 Q218 300 200 320 Q182 340 164 320 Q146 300 128 320 Q110 340 90 320 Z", fill }, h);
      el("ellipse", { cx: 165, cy: 130, rx: 45, ry: 24, fill: "url(#av-shine)", transform: "rotate(-18 165 130)" }, h);
    }
  }

  drawAccessory() {
    const c = this.cfg;
    const a = c.accessory;
    if (a === "antenna") {
      const top = c.form === "bot" ? 95 : 85;
      el("line", { x1: 200, y1: top, x2: 200, y2: top - 45, stroke: shade(c.primary, -0.3), "stroke-width": 6, "stroke-linecap": "round" }, this.behind);
      this.antennaBall = el("circle", { cx: 200, cy: top - 52, r: 12, fill: c.accent }, this.front);
    } else if (a === "halo") {
      this.haloRing = el("ellipse", { cx: 200, cy: 62, rx: 70, ry: 16, fill: "none", stroke: "#ffe27a", "stroke-width": 7, opacity: .9 }, this.front);
    } else if (a === "ears") {
      el("path", { d: "M110 150 L118 60 L180 112 Z", fill: shade(c.primary, -0.1) }, this.behind);
      el("path", { d: "M290 150 L282 60 L220 112 Z", fill: shade(c.primary, -0.1) }, this.behind);
    } else if (a === "headphones") {
      el("path", { d: "M85 205 C85 90 315 90 315 205", fill: "none", stroke: "#222a", "stroke-width": 14, "stroke-linecap": "round" }, this.front);
      el("rect", { x: 66, y: 180, width: 36, height: 70, rx: 16, fill: c.accent }, this.front);
      el("rect", { x: 298, y: 180, width: 36, height: 70, rx: 16, fill: c.accent }, this.front);
    } else if (a === "crown") {
      el("path", { d: "M140 95 L150 45 L175 75 L200 35 L225 75 L250 45 L260 95 Z", fill: "#ffd54a", stroke: "#e0a800", "stroke-width": 3 }, this.front);
      el("circle", { cx: 200, cy: 72, r: 7, fill: "#ff3b5c" }, this.front);
    }
  }

  drawEyes() {
    const c = this.cfg, f = this.face;
    const ey = c.form === "bot" ? 185 : 185, ex = 45;
    this.eyes = [];
    const ink = c.form === "bot" ? c.accent : "#fff";
    for (const s of [-1, 1]) {
      const g = el("g", {}, f);
      const cx = 200 + s * ex;
      if (c.eyes === "round") {
        const white = el("ellipse", { cx, cy: ey, rx: 24, ry: 28, fill: "#fff" }, g);
        const pupil = el("circle", { cx, cy: ey, r: 13, fill: "#16122b" }, g);
        const spark = el("circle", { cx: cx + 5, cy: ey - 7, r: 4, fill: "#fff" }, g);
        this.eyes.push({ g, cx, cy: ey, white, pupil, spark, kind: "round" });
      } else if (c.eyes === "visor") {
        if (s === 1) {
          const bar = el("rect", { x: 135, y: ey - 14, width: 130, height: 28, rx: 14, fill: ink, opacity: .95 }, g);
          const dot = el("rect", { x: 190, y: ey - 10, width: 22, height: 20, rx: 10, fill: "#fff" }, g);
          this.eyes.push({ g, cx: 200, cy: ey, bar, dot, kind: "visor" });
        }
      } else if (c.eyes === "sleepy") {
        const p = el("path", { d: `M${cx - 22} ${ey} Q${cx} ${ey + 16} ${cx + 22} ${ey}`, stroke: ink, "stroke-width": 7, fill: "none", "stroke-linecap": "round" }, g);
        this.eyes.push({ g, cx, cy: ey, p, kind: "sleepy" });
      } else if (c.eyes === "star") {
        const pts = [];
        for (let i = 0; i < 10; i++) {
          const r = i % 2 ? 9 : 22, a = Math.PI / 5 * i - Math.PI / 2;
          pts.push(`${(cx + r * Math.cos(a)).toFixed(1)},${(ey + r * Math.sin(a)).toFixed(1)}`);
        }
        const star = el("polygon", { points: pts.join(" "), fill: c.form === "bot" ? c.accent : "#fff6a8" }, g);
        this.eyes.push({ g, cx, cy: ey, star, kind: "star" });
      } else {
        const d = el("circle", { cx, cy: ey, r: 11, fill: c.form === "bot" ? c.accent : "#16122b" }, g);
        this.eyes.push({ g, cx, cy: ey, d, kind: "dot" });
      }
    }
  }

  frame(now) {
    requestAnimationFrame(t => this.frame(t));
    if (!this.cfg) return;
    const t = (now - this.t0) / 1000, s = this.state, c = this.cfg;

    // gaze: follow pointer recently moved, else wander; thinking looks up-right
    if (s === "thinking") this.lookTarget = { x: 0.6, y: -0.7 };
    else if (s === "working") this.lookTarget = { x: Math.sin(t * 1.3) * 0.6, y: 0.3 };
    else if (!this.lastPointer || now - this.lastPointer > 4000) this.lookTarget = { x: Math.sin(t * 0.35) * 0.4, y: Math.sin(t * 0.23) * 0.25 };
    this.look.x += (this.lookTarget.x - this.look.x) * 0.08;
    this.look.y += (this.lookTarget.y - this.look.y) * 0.08;

    // blink
    if (now > this.nextBlink) { this.blink = 1; this.nextBlink = now + 2500 + Math.random() * 3500; }
    this.blink = Math.max(0, this.blink - 0.12);
    const lid = this.blink > 0 ? Math.sin(this.blink * Math.PI) : 0;

    // mouth openness follows speech amplitude
    let target = s === "speaking" ? this.amplitude : 0;
    this.mouthOpen += (target - this.mouthOpen) * 0.35;

    // body motion
    const bob = Math.sin(t * (s === "speaking" ? 3 : 1.6)) * (s === "idle" ? 6 : 4);
    const breathe = 1 + Math.sin(t * 1.6) * 0.012 + (s === "listening" ? 0.03 : 0) + this.mouthOpen * 0.02;
    const tilt = s === "thinking" ? 6 : s === "listening" ? -4 : Math.sin(t * 0.7) * 1.5;
    const size = c.size || 1;
    this.root.setAttribute("transform", `translate(200 ${200 + bob}) rotate(${tilt}) scale(${breathe * size}) translate(-200 -200)`);
    this.halo.setAttribute("r", 175 * size + Math.sin(t * 2) * 6 + this.mouthOpen * 20 + (s === "listening" ? 15 : 0));

    // face shift with gaze
    this.face.setAttribute("transform", `translate(${this.look.x * 10} ${this.look.y * 8})`);
    for (const e of this.eyes) {
      const sy = Math.max(0.08, 1 - lid) * (s === "listening" ? 1.12 : s === "happy" ? 0.5 : 1);
      e.g.setAttribute("transform", `translate(${e.cx} ${e.cy}) scale(1 ${sy}) translate(${-e.cx} ${-e.cy})`);
      if (e.kind === "round") {
        const px = e.cx + this.look.x * 8, py = e.cy + this.look.y * 9;
        e.pupil.setAttribute("cx", px); e.pupil.setAttribute("cy", py);
        e.spark.setAttribute("cx", px + 5); e.spark.setAttribute("cy", py - 6);
      } else if (e.kind === "visor") {
        e.dot.setAttribute("x", 189 + this.look.x * 45 + (s === "working" ? Math.sin(t * 6) * 40 : 0));
      } else if (e.kind === "star") {
        e.star.setAttribute("transform", `rotate(${(t * 40) % 360} ${e.cx} ${e.cy})`);
      }
    }

    if (this.mouth) {
      const o = this.mouthOpen, mx = 200, my = this.cfg.form === "bot" ? 228 : 240;
      const w = 26 + o * 8, h = 3 + o * 26, smile = s === "error" ? -8 : s === "happy" ? 12 : 6;
      this.mouth.setAttribute("d", `M${mx - w} ${my} Q${mx} ${my + smile + h} ${mx + w} ${my} Q${mx} ${my + smile * 0.3 + h * 0.25} ${mx - w} ${my} Z`);
    }

    // listening: expanding rings
    this.ringEls.forEach((r, i) => {
      if (s === "listening" || s === "waiting") {
        const p = ((t * (s === "waiting" ? 0.5 : 0.9)) + i / 3) % 1;
        r.setAttribute("r", 120 * size + p * 80);
        r.setAttribute("opacity", (1 - p) * 0.7);
        r.setAttribute("stroke", s === "waiting" ? "#ffb020" : c.accent);
      } else r.setAttribute("opacity", 0);
    });
    // thinking: orbiting dots
    this.orbitDots.forEach((d, i) => {
      const on = s === "thinking";
      const a = t * 3 - i * 0.45;
      d.setAttribute("cx", 200 + Math.cos(a) * 160 * size);
      d.setAttribute("cy", 200 + Math.sin(a) * 160 * size);
      d.setAttribute("opacity", on ? 0.9 - i * 0.25 : 0);
    });
    // working: spinning dashed ring
    this.spinner.setAttribute("opacity", s === "working" ? 0.8 : 0);
    this.spinner.setAttribute("transform", `rotate(${(t * 120) % 360} 200 200)`);
    this.spinner.setAttribute("r", 150 * size);

    if (this.antennaBall) this.antennaBall.setAttribute("opacity", 0.55 + 0.45 * Math.abs(Math.sin(t * (s === "idle" ? 1.5 : 5))));
    if (this.haloRing) this.haloRing.setAttribute("transform", `translate(0 ${Math.sin(t * 2) * 4})`);
    this.flash = Math.max(0, this.flash - 0.02);
    this.tint.setAttribute("opacity", this.flash * 0.35);
  }
}
