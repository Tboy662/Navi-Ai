"""Regenerates the two soft UI sounds in sounds/ (stdlib only).

Run from the repo root:  python tools/make_sounds.py
"""
import math
import os
import struct
import wave

RATE = 44100
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sounds")


def tone(freq, dur, amp, attack=0.012, decay=6.0):
    #sine with a tiny bit of octave for warmth, quick fade in, exponential fade out
    n = int(RATE * dur)
    out = []
    for i in range(n):
        t = i / RATE
        env = min(1.0, t / attack) * math.exp(-decay * t)
        env *= min(1.0, (n - i) / (RATE * 0.01))  #no click at the end
        s = math.sin(2 * math.pi * freq * t) + 0.15 * math.sin(4 * math.pi * freq * t)
        out.append(amp * env * s / 1.15)
    return out


def mix(base, other, offset):
    start = int(RATE * offset)
    need = start + len(other)
    if len(base) < need:
        base = base + [0.0] * (need - len(base))
    for i, s in enumerate(other):
        base[start + i] += s
    return base


def save(name, samples):
    os.makedirs(OUT, exist_ok=True)
    with wave.open(os.path.join(OUT, name), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, s)) * 32767)) for s in samples))


#thinking: two quiet, quick rising blips (like a soft "hm-hm")
thinking = tone(660, 0.09, 0.14, decay=30)
thinking = mix(thinking, tone(880, 0.12, 0.12, decay=25), 0.085)
save("thinking.wav", thinking + [0.0] * int(RATE * 0.03))

#switching mode: a gentle two-note chime (C5 then G5) that rings out softly
chime = tone(523.25, 0.30, 0.16, decay=9)
chime = mix(chime, tone(783.99, 0.40, 0.14, decay=8), 0.11)
save("mode_switch.wav", chime + [0.0] * int(RATE * 0.03))
