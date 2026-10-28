"""把曲谱合成“旋律小样”：主旋律用带颤音的柔和音色（像哼唱，模型更容易把它当成人声旋律），
和弦用小音量垫底，再加一层简单的节拍。纯 Python，不依赖第三方库；音质够模型听清旋律就行。"""
import array
import math
import random
import sys
import wave

RATE = 22050


def _table(harmonics, size=2048):
    t = [sum(a * math.sin(2 * math.pi * k * i / size) for k, a in harmonics) for i in range(size)]
    peak = max(abs(x) for x in t)
    return [x / peak for x in t]


LEAD = _table([(1, 1.0), (2, 0.35), (3, 0.2), (4, 0.08)])
PAD = _table([(1, 1.0), (2, 0.5), (3, 0.25), (4, 0.12), (5, 0.06)])


def hz(pitch):
    return 440.0 * 2 ** ((pitch - 69) / 12)


def _tone(buf, t0, dur, freq, table, amp, attack, release, vibrato=0.0):
    """在 buf 里叠加一个音：起音 attack 秒、收尾 release 秒，vibrato 是颤音深度（0.4 秒后渐入）。"""
    n0, n = int(t0 * RATE), int(dur * RATE)
    if n <= 0:
        return
    size = len(table)
    a, r = max(1, int(attack * RATE)), max(1, int(min(release, dur / 2) * RATE))
    inc, phase = freq * size / RATE, 0.0
    w, ramp = 2 * math.pi * 5.5 / RATE, 0.4 * RATE
    for i in range(n):
        env = i / a if i < a else (n - i) / r if n - i < r else 1.0
        step = inc * (1 + vibrato * min(1.0, i / ramp) * math.sin(w * i)) if vibrato else inc
        buf[n0 + i] += amp * env * table[int(phase) % size]
        phase += step


def _kick(buf, t0):
    n0, n, phase = int(t0 * RATE), int(0.18 * RATE), 0.0
    for i in range(n):
        x = i / n
        phase += 2 * math.pi * (50 + 70 * (1 - x) ** 3) / RATE
        buf[n0 + i] += 0.45 * (1 - x) ** 2 * math.sin(phase)


def _hat(buf, t0, rng):
    n0, n, prev = int(t0 * RATE), int(0.04 * RATE), 0.0
    for i in range(n):
        x = rng.uniform(-1, 1)
        buf[n0 + i] += 0.07 * (1 - i / n) * (x - prev)      # 一阶差分，近似高通的“嚓”
        prev = x


def render(score, path, chords=True, beat=True):
    """把 score 合成单声道 WAV，返回时长（秒）。"""
    spb = 60 / score.tempo
    total = score.beats * spb + 1.0                          # 结尾留 1 秒余音
    buf = array.array('f', bytes(4 * (int(total * RATE) + RATE)))
    for note in score.notes:
        if note.pitch is not None:
            _tone(buf, note.start * spb, note.beats * spb, hz(note.pitch), LEAD, 0.5, 0.02, 0.05, vibrato=0.004)
    if chords:
        for chord in score.chords:
            for p in chord.pitches:
                _tone(buf, chord.start * spb, chord.beats * spb, hz(p), PAD, 0.1, 0.05, 0.2)
    if beat:
        rng, bpb = random.Random(0), score.beats_per_bar
        t = score.pickup % 1
        while t < score.beats:
            in_bar = (t - score.pickup) % bpb
            if in_bar < 1e-6 or (bpb == 4 and abs(in_bar - 2) < 1e-6):
                _kick(buf, t * spb)
            _hat(buf, t * spb, rng)
            t += 1

    peak = max(max(buf), -min(buf)) or 1.0
    pcm = array.array('h', (int(x * 0.9 * 32767 / peak) for x in buf[:int(total * RATE)]))
    if sys.byteorder == 'big':
        pcm.byteswap()
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return total
