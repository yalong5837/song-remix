import array
import math
import tempfile
import unittest
import wave
from pathlib import Path

from remixer import score, synth


def read_wav(path):
    with wave.open(str(path)) as w:
        return array.array('h', w.readframes(w.getnframes())), w.getframerate()


def power(samples, freq, rate):
    """Goertzel：某个频率上的能量。"""
    k, s1, s2 = 2 * math.cos(2 * math.pi * freq / rate), 0.0, 0.0
    for x in samples:
        s1, s2 = x + k * s1 - s2, s1
    return s1 * s1 + s2 * s2 - k * s1 * s2


class SynthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_pitches_follow_the_score(self):
        s = score.parse_jianpu('速度: 60\n| 1 - - - | 5 - - - |')     # 前 4 秒 C4，后 4 秒 G4
        path = self.dir / 'a.wav'
        self.assertEqual(synth.render(s, path, chords=False, beat=False), 9)
        pcm, rate = read_wav(path)
        self.assertEqual(len(pcm), 9 * rate)
        c4, g4 = synth.hz(60), synth.hz(67)
        first, second = pcm[1 * rate:2 * rate], pcm[5 * rate:6 * rate]
        self.assertGreater(power(first, c4, rate), 20 * power(first, g4, rate))
        self.assertGreater(power(second, g4, rate), 20 * power(second, c4, rate))

    def test_full_mix_is_normalized(self):
        s = score.parse_jianpu('| 1 3 5 1\' |\n和弦: C |')
        path = self.dir / 'b.wav'
        synth.render(s, path)
        pcm, _ = read_wav(path)
        self.assertAlmostEqual(max(max(pcm), -min(pcm)), 0.9 * 32767, delta=2)

    def test_beat_only_track_is_not_silent(self):
        s = score.parse_jianpu('| 0 0 0 0 | 0 0 0 0 |')
        path = self.dir / 'c.wav'
        synth.render(s, path, chords=False)
        pcm, rate = read_wav(path)
        self.assertGreater(max(pcm), 1000)
        self.assertEqual(len(pcm), int((8 * 60 / 90 + 1) * rate))     # 8 拍、每分钟 90 拍，再加 1 秒余音
