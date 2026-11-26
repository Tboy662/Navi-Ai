"""Soft UI sounds for Navi: "thinking" and "switching mode".

Playback never blocks and never raises, so a missing file or audio device
can't break the assistant. The WAVs are already mixed quietly; on Mac afplay
also gets a lower volume. Set SOUNDS_ENABLED=False to mute them.
"""
import os
import subprocess
import sys
import threading
import time

SOUNDS_ENABLED = True
MAC_VOLUME = "0.6"  #afplay -v, 1.0 is normal volume
_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sounds")
_FILES = {"thinking": "thinking.wav", "mode": "mode_switch.wav"}
_last = {}
_lock = threading.Lock()


def play(name, min_gap=0.4):
    """Play "thinking" or "mode" in the background. Repeats within min_gap seconds are skipped."""
    if not SOUNDS_ENABLED or name not in _FILES:
        return
    with _lock:
        now = time.monotonic()
        if now - _last.get(name, -min_gap) < min_gap:
            return
        _last[name] = now
    path = os.path.join(_DIR, _FILES[name])
    if not os.path.exists(path):
        return
    try:
        if sys.platform == "win32":
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        elif sys.platform == "darwin":
            subprocess.Popen(["afplay", "-v", MAC_VOLUME, path],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            for player in (["paplay", path], ["aplay", "-q", path]):
                try:
                    subprocess.Popen(player, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    break
                except FileNotFoundError:
                    continue
    except Exception:
        pass
