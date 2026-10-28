import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from fakes import make_tone, make_video

from remixer import audio


def loudness(path):
    """用 ffmpeg 的 ebur128 量整体响度（LUFS）。"""
    r = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-i', str(path), '-af', 'ebur128', '-f', 'null', '-'],
                       capture_output=True, text=True)
    return float(re.findall(r'I:\s+(-?[\d.]+) LUFS', r.stderr)[-1])


class TimeTest(unittest.TestCase):
    def test_parse(self):
        self.assertIsNone(audio.parse_time(None))
        self.assertEqual(audio.parse_time('65'), 65)
        self.assertEqual(audio.parse_time('1:05'), 65)
        self.assertEqual(audio.parse_time('1:02:03.5'), 3723.5)
        self.assertEqual(audio.parse_time(30), 30)
        with self.assertRaisesRegex(ValueError, '时间格式'):
            audio.parse_time('1分05')
        with self.assertRaisesRegex(ValueError, '负数'):
            audio.parse_time('-3')

    def test_fmt(self):
        self.assertEqual(audio.fmt_time(65.4), '1:05')
        self.assertEqual(audio.fmt_time(5), '0:05')


@unittest.skipUnless(shutil.which('ffmpeg'), '需要 ffmpeg')
class AudioTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.song = cls.dir / 'song.wav'
        make_tone(cls.song, 12)
        cls.video = cls.dir / 'clip.mp4'
        make_video(cls.video, 4)
        cls.silent = cls.dir / 'silent.mp4'
        make_video(cls.silent, 4, with_audio=False)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def assertLength(self, path, sec):
        self.assertAlmostEqual(audio.duration(path), sec, delta=0.15)

    def test_clip(self):
        dst = self.dir / 'c1.mp3'
        self.assertEqual(audio.clip(self.song, dst, 2, 5), 5)
        self.assertLength(dst, 5)

    def test_clip_stops_at_end(self):
        dst = self.dir / 'c2.mp3'
        self.assertAlmostEqual(audio.clip(self.song, dst, 10, 30), 2, delta=0.01)
        self.assertLength(dst, 2)

    def test_clip_start_past_end(self):
        with self.assertRaisesRegex(ValueError, '超出'):
            audio.clip(self.song, self.dir / 'c3.mp3', 20, 5)

    def test_finish_trims_and_normalizes(self):
        dst = self.dir / 'f1.m4a'
        self.assertEqual(audio.finish(self.song, dst, 6, fade_in=0.5), 6)
        self.assertLength(dst, 6)
        self.assertAlmostEqual(loudness(dst), -14, delta=1.5)

    def test_finish_rejects_unknown_format(self):
        with self.assertRaisesRegex(ValueError, '.ogg'):
            audio.finish(self.song, self.dir / 'f2.ogg')

    def test_mux_replaces_audio_and_matches_video_length(self):
        for music_sec in (2, 10):     # 配乐比视频短（补静音）/ 比视频长（截断）
            music = self.dir / f'm{music_sec}.mp3'
            make_tone(music, music_sec)
            dst = self.dir / f'v{music_sec}.mp4'
            audio.mux(self.video, music, dst)
            self.assertLength(dst, 4)
            self.assertTrue(audio.has_audio(dst))

    def test_mux_keeps_voice(self):
        music = self.dir / 'm3.mp3'
        make_tone(music, 3, freq=880)
        dst = self.dir / 'keep.mp4'
        audio.mux(self.video, music, dst, music_volume=0.3, keep_video_audio=True)
        self.assertLength(dst, 4)
        self.assertTrue(audio.has_audio(dst))

    def test_mux_keep_on_silent_video_falls_back(self):
        music = self.dir / 'm4.mp3'
        make_tone(music, 5)
        dst = self.dir / 'silent-out.mp4'
        audio.mux(self.silent, music, dst, keep_video_audio=True)
        self.assertLength(dst, 4)
        self.assertTrue(audio.has_audio(dst))
        self.assertFalse(audio.has_audio(self.silent))


if __name__ == '__main__':
    unittest.main()
