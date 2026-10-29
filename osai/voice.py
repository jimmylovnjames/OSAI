"""Optional on-device speech: Whisper for speech-to-text, Piper/espeak for text-to-speech.

When neither is installed, the UI falls back to the browser's Web Speech API.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import threading

from .osinfo import which

_whisper = None
_whisper_lock = threading.Lock()


def stt_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def transcribe(audio: bytes, model_name: str = "base.en", suffix: str = ".webm") -> str:
    global _whisper
    from faster_whisper import WhisperModel  # type: ignore
    with _whisper_lock:
        if _whisper is None:
            _whisper = WhisperModel(model_name, device="auto", compute_type="int8")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(audio)
        path = f.name
    try:
        segments, _ = _whisper.transcribe(path, vad_filter=True, beam_size=1)
        return " ".join(s.text.strip() for s in segments).strip()
    finally:
        os.unlink(path)


def tts_backend(piper_model: str = "") -> str | None:
    if piper_model and which("piper") and os.path.exists(os.path.expanduser(piper_model)):
        return "piper"
    if which("espeak-ng", "espeak"):
        return "espeak"
    return None


def synthesize(text: str, piper_model: str = "", rate: float = 1.0) -> bytes:
    """Return WAV bytes."""
    backend = tts_backend(piper_model)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        out = f.name
    try:
        if backend == "piper":
            subprocess.run(["piper", "--model", os.path.expanduser(piper_model), "--output_file", out,
                            "--length_scale", f"{1 / max(rate, 0.3):.2f}"], input=text, text=True, check=True,
                           capture_output=True, timeout=60)
        elif backend == "espeak":
            exe = which("espeak-ng", "espeak")
            subprocess.run([exe, "-s", str(int(170 * rate)), "-w", out, text], check=True, capture_output=True, timeout=60)
        else:
            raise RuntimeError("no server TTS available")
        with open(out, "rb") as f:
            return f.read()
    finally:
        if os.path.exists(out):
            os.unlink(out)
