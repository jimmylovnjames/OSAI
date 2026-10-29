// Voice I/O for OSAI.
// Listening: on-device Whisper via the server when installed, else the browser's Web Speech API.
// Speaking: browser speechSynthesis, or server TTS (Piper/espeak) when the browser has no voices.
// Speech is spoken sentence-by-sentence while the reply streams in, so OSAI starts talking immediately.

const WAKE_VARIANTS = w => [w, `hey ${w}`, `ok ${w}`, "o sai", "oh sigh", "osay", "o.s.a.i", "os ai", "hey o sai"];

export class Voice {
  constructor({ onTranscript, onInterim, onListening, onSpeaking, onAmplitude, getConfig, serverCaps, token }) {
    Object.assign(this, { onTranscript, onInterim, onListening, onSpeaking, onAmplitude, getConfig, serverCaps, token });
    this.queue = [];
    this.buffer = "";
    this.speaking = false;
    this.listening = false;
    this.handsFree = false;
    this.followUpUntil = 0;
    this.audioCtx = null;
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    this.SR = SR || null;
  }

  get cfg() { return this.getConfig().voice || {}; }

  sttMode() {
    const m = this.cfg.stt || "auto";
    if (m === "server" || (m === "auto" && this.serverCaps.server_stt)) return "server";
    if (this.SR) return "browser";
    return this.serverCaps.server_stt ? "server" : "none";
  }

  // ---------------------------------------------------------------- listening
  async listenOnce() {
    if (this.listening) return this.stopListening();
    this.stopSpeaking();
    const mode = this.sttMode();
    if (mode === "none") { this.onInterim("Voice input isn't available in this browser - type instead, or install faster-whisper."); return; }
    this.listening = true;
    this.onListening(true);
    try {
      const text = mode === "server" ? await this.recordAndTranscribe() : await this.browserRecognize();
      if (text) this.onTranscript(text, false);
    } catch (e) {
      this.onInterim(`Didn't catch that (${e.message || e}).`);
    } finally {
      this.listening = false;
      this.onListening(false);
      if (this.handsFree) setTimeout(() => this.handsFreeLoop(), 300);
    }
  }

  stopListening() {
    this._stop?.();
  }

  setHandsFree(on) {
    this.handsFree = on;
    if (on && !this.listening && !this.speaking) this.handsFreeLoop();
    if (!on) this.stopListening();
  }

  async handsFreeLoop() {
    if (!this.handsFree || this.listening || this.speaking) return;
    const mode = this.sttMode();
    if (mode === "none") return;
    this.listening = true;
    this.onListening(true, true);
    let text = "";
    try {
      text = mode === "server" ? await this.recordAndTranscribe(true) : await this.browserRecognize(true);
    } catch { /* silence or error: just loop */ }
    this.listening = false;
    this.onListening(false);
    if (text) {
      const stripped = this.matchWake(text);
      if (stripped !== null) this.onTranscript(stripped || "", true);
    }
    if (this.handsFree) setTimeout(() => this.handsFreeLoop(), 250);
  }

  // Returns the request without the wake word, "" if only the wake word, or null if not addressed to OSAI.
  matchWake(text) {
    const t = text.trim().toLowerCase().replace(/[,.!?]/g, "");
    if (Date.now() < this.followUpUntil) return text.trim(); // conversation mode right after OSAI spoke
    const w = (this.cfg.wake_word || "osai").toLowerCase();
    for (const v of WAKE_VARIANTS(w).sort((a, b) => b.length - a.length)) {
      if (t.startsWith(v)) return text.trim().slice(v.length).replace(/^[\s,.!?]+/, "");
    }
    return null;
  }

  browserRecognize(continuous = false) {
    return new Promise((resolve, reject) => {
      const r = new this.SR();
      r.lang = navigator.language || "en-US";
      r.interimResults = true;
      r.continuous = false;
      let final = "", done = false;
      const finish = (v, err) => { if (done) return; done = true; this._stop = null; err ? reject(err) : resolve(v); };
      r.onresult = e => {
        let interim = "";
        for (const res of e.results) (res.isFinal ? (final = res[0].transcript) : (interim += res[0].transcript));
        if (!continuous || this.matchWake(final || interim) !== null) this.onInterim(final || interim, true);
      };
      r.onerror = e => (e.error === "no-speech" || e.error === "aborted") ? finish("") : finish("", new Error(e.error));
      r.onend = () => finish(final);
      this._stop = () => r.stop();
      r.start();
    });
  }

  async recordAndTranscribe(continuous = false) {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    const ctx = this.ctx();
    const src = ctx.createMediaStreamSource(stream);
    const an = ctx.createAnalyser();
    an.fftSize = 1024;
    src.connect(an);
    const rec = new MediaRecorder(stream);
    const chunks = [];
    rec.ondataavailable = e => e.data.size && chunks.push(e.data);
    const buf = new Float32Array(an.fftSize);
    let heard = false, lastVoice = performance.now(), started = performance.now();
    const blob = await new Promise(resolve => {
      let stopped = false;
      const stop = () => { if (!stopped) { stopped = true; rec.state !== "inactive" && rec.stop(); } };
      this._stop = stop;
      rec.onstop = () => resolve(new Blob(chunks, { type: rec.mimeType }));
      rec.start(250);
      const tick = () => {
        if (stopped) return;
        an.getFloatTimeDomainData(buf);
        let rms = 0; for (const v of buf) rms += v * v; rms = Math.sqrt(rms / buf.length);
        this.onAmplitude?.(Math.min(1, rms * 8));
        const now = performance.now();
        if (rms > 0.02) { heard = true; lastVoice = now; }
        if ((heard && now - lastVoice > 900) || (!heard && now - started > (continuous ? 12000 : 7000)) || now - started > 30000) return stop();
        requestAnimationFrame(tick);
      };
      tick();
    });
    stream.getTracks().forEach(t => t.stop());
    this._stop = null;
    if (!heard) return "";
    const res = await fetch("/api/stt", { method: "POST", body: blob, headers: { "content-type": blob.type, "x-osai-token": this.token } });
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    return (await res.json()).text;
  }

  ctx() {
    if (!this.audioCtx) this.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (this.audioCtx.state === "suspended") this.audioCtx.resume();
    return this.audioCtx;
  }

  // ---------------------------------------------------------------- speaking
  ttsMode() {
    const m = this.cfg.tts || "auto";
    if (m === "off") return "off";
    const hasVoices = "speechSynthesis" in window && speechSynthesis.getVoices().length > 0;
    if (m === "server" || (m === "auto" && !hasVoices && this.serverCaps.server_tts)) return "server";
    return "speechSynthesis" in window ? "browser" : (this.serverCaps.server_tts ? "server" : "off");
  }

  // Feed streamed reply text; complete sentences are spoken right away.
  feed(delta) {
    this.replyDone = false;
    this.buffer += delta;
    const m = this.buffer.match(/^([\s\S]*?[.!?…:;])(\s+|$)(?=[\s\S])/);
    if (m && m[1].trim().length > 1) {
      this.buffer = this.buffer.slice(m[0].length);
      this.say(m[1]);
    }
  }

  flush() {
    this.replyDone = true;
    const rest = this.buffer.trim();
    this.buffer = "";
    if (rest) this.say(rest);
  }

  say(text) {
    if (this.replyDone === undefined) this.replyDone = true;
    const clean = text.replace(/```[\s\S]*?```/g, " ").replace(/https?:\/\/\S+/g, "a link").replace(/[*_#`>|]/g, "").trim();
    if (!clean || this.ttsMode() === "off") return;
    this.queue.push(clean);
    if (!this.speaking) this.next();
  }

  async next() {
    const text = this.queue.shift();
    if (!text) {
      this.speaking = false;
      this.onSpeaking(false);
      if (this.replyDone === false) return; // more of the reply is still streaming in
      this.followUpUntil = Date.now() + 8000;
      if (this.handsFree) setTimeout(() => this.handsFreeLoop(), 200);
      return;
    }
    if (!this.speaking) { this.speaking = true; this.onSpeaking(true); this.stopListening(); }
    try {
      if (this.ttsMode() === "server") await this.speakServer(text);
      else await this.speakBrowser(text);
    } catch { /* keep going */ }
    this.next();
  }

  speakBrowser(text) {
    return new Promise(resolve => {
      const u = new SpeechSynthesisUtterance(text);
      const v = this.cfg;
      u.rate = v.rate || 1; u.pitch = v.pitch ?? 1;
      const voice = speechSynthesis.getVoices().find(x => x.name === v.voice_name);
      if (voice) u.voice = voice;
      let alive = true;
      // No audio access for browser TTS: drive the mouth from word boundaries plus a little noise.
      u.onboundary = () => { this.onAmplitude?.(0.9); };
      const wobble = () => { if (!alive) return; this.onAmplitude?.(0.25 + Math.random() * 0.55); setTimeout(wobble, 90); };
      u.onstart = wobble;
      u.onend = u.onerror = () => { alive = false; this.onAmplitude?.(0); resolve(); };
      speechSynthesis.speak(u);
    });
  }

  async speakServer(text) {
    const res = await fetch("/api/tts", { method: "POST", headers: { "content-type": "application/json", "x-osai-token": this.token }, body: JSON.stringify({ text }) });
    if (!res.ok) throw new Error("tts failed");
    const ctx = this.ctx();
    const audio = await ctx.decodeAudioData(await res.arrayBuffer());
    const src = ctx.createBufferSource();
    src.buffer = audio;
    const an = ctx.createAnalyser();
    an.fftSize = 512;
    src.connect(an); an.connect(ctx.destination);
    const buf = new Float32Array(an.fftSize);
    let alive = true;
    const tick = () => {
      if (!alive) return;
      an.getFloatTimeDomainData(buf);
      let rms = 0; for (const v of buf) rms += v * v;
      this.onAmplitude?.(Math.min(1, Math.sqrt(rms / buf.length) * 6));
      requestAnimationFrame(tick);
    };
    this._source = src;
    await new Promise(r => { src.onended = r; src.start(); tick(); });
    alive = false;
    this.onAmplitude?.(0);
  }

  stopSpeaking() {
    this.queue = [];
    this.buffer = "";
    if ("speechSynthesis" in window) speechSynthesis.cancel();
    try { this._source?.stop(); } catch {}
    if (this.speaking) { this.speaking = false; this.onSpeaking(false); }
  }
}
