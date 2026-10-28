import contextlib
import io
import json
import os
import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from fakes import FakeHTTP, FakeResponse, make_midi, make_tone, make_video

import remix
from remixer import audio


def run(argv, http=None):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = remix.main(argv, http=http if http is not None else FakeHTTP())
    return code, out.getvalue(), err.getvalue()


def ok(data):
    return FakeResponse({'code': 200, 'msg': 'success', 'data': data})


class PresetTest(unittest.TestCase):
    def test_presets_are_valid(self):
        with open(remix.HERE / 'presets.toml', 'rb') as f:
            presets = tomllib.load(f)
        self.assertIn('citypop', presets)
        for name, p in presets.items():
            self.assertTrue(p.get('style'), name)
            self.assertTrue(p.get('desc'), name)
            self.assertLessEqual(0, p.get('keep', 0.5))
            self.assertLessEqual(p.get('keep', 0.5), 1)

    def test_styles_command(self):
        code, out, _ = run(['styles'])
        self.assertEqual(code, 0)
        self.assertIn('citypop', out)
        self.assertIn('city pop', out)


@unittest.skipUnless(shutil.which('ffmpeg'), '需要 ffmpeg')
class CliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.song = cls.dir / '老歌.wav'
        make_tone(cls.song, 20)
        cls.video = cls.dir / '视频.mp4'
        make_video(cls.video, 5)
        cls.lyrics = cls.dir / '副歌.txt'
        cls.lyrics.write_text('[chorus]\n你问我爱你有多深\n', encoding='utf-8')
        result = cls.dir / 'result.mp3'
        make_tone(result, 9, freq=660)
        cls.result_bytes = result.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def out_dir(self):
        d = tempfile.mkdtemp(dir=self.dir)
        return d, lambda: list(Path(d).iterdir())

    def test_dry_run_prints_payload_without_network(self):
        out, made = self.out_dir()
        http = FakeHTTP()
        code, stdout, _ = run(['cover', str(self.song), '--style', 'lofi', '--start', '0:02', '--duration', '6',
                               '--lyrics', str(self.lyrics), '--set', 'styleWeight=0.7', '--model', 'V5',
                               '--dry-run', '--out', out], http)
        self.assertEqual(code, 0)
        self.assertEqual(http.calls, [])
        payload = json.loads(stdout[stdout.index('{'):stdout.rindex('}') + 1])
        self.assertEqual(payload['uploadUrl'], remix.PLACEHOLDER)
        self.assertEqual(payload['prompt'], '[chorus]\n你问我爱你有多深')
        self.assertTrue(payload['style'].startswith('lo-fi hip hop'))
        self.assertEqual(payload['audioWeight'], 0.6)
        self.assertEqual(payload['styleWeight'], 0.7)
        self.assertEqual(payload['title'], '老歌 (lofi)')
        self.assertIn('0:02–0:08', stdout)
        (run_dir,) = made()
        self.assertAlmostEqual(audio.duration(run_dir / 'source.mp3'), 6, delta=0.15)

    def test_free_text_style_and_video_length(self):
        out, made = self.out_dir()
        code, stdout, _ = run(['cover', str(self.song), '--style', 'bossa nova, nylon guitar',
                               '--more', 'female vocals', '--video', str(self.video),
                               '--dry-run', '--out', out])
        self.assertEqual(code, 0)
        payload = json.loads(stdout[stdout.index('{'):stdout.rindex('}') + 1])
        self.assertEqual(payload['prompt'], 'bossa nova, nylon guitar, female vocals')
        (run_dir,) = made()
        self.assertIn('custom', run_dir.name)
        # 视频 5 秒，多截 2 秒
        self.assertAlmostEqual(audio.duration(run_dir / 'source.mp3'), 7, delta=0.15)

    def test_minimax_instrumental_fails_before_clipping(self):
        out, made = self.out_dir()
        code, _, err = run(['cover', str(self.song), '--provider', 'minimax', '--instrumental',
                            '--dry-run', '--out', out])
        self.assertEqual(code, 1)
        self.assertIn('纯伴奏', err)
        self.assertEqual(made(), [])

    def test_minimax_clip_too_short(self):
        out, _ = self.out_dir()
        code, _, err = run(['cover', str(self.song), '--provider', 'minimax', '--duration', '3',
                            '--dry-run', '--out', out])
        self.assertEqual(code, 1)
        self.assertIn('6~360', err)

    def test_keep_out_of_range(self):
        code, _, err = run(['cover', str(self.song), '--keep', '1.5', '--dry-run'])
        self.assertEqual(code, 1)
        self.assertIn('0~1', err)

    def test_missing_key(self):
        out, _ = self.out_dir()
        with mock.patch.dict(os.environ, {'SUNO_API_KEY': ''}), \
                mock.patch.object(remix, 'load_toml', return_value={}):
            code, _, err = run(['cover', str(self.song), '--out', out])
        self.assertEqual(code, 1)
        self.assertIn('SUNO_API_KEY', err)

    def test_missing_source(self):
        code, _, err = run(['cover', str(self.dir / 'nope.mp3'), '--dry-run'])
        self.assertEqual(code, 1)
        self.assertIn('找不到原曲', err)

    def test_full_suno_flow_with_video(self):
        out, made = self.out_dir()
        http = FakeHTTP([
            ('POST', 'file-stream-upload', ok({'downloadUrl': 'https://tmp/source.mp3'})),
            ('POST', 'upload-cover', ok({'taskId': 'task-123'})),
            ('GET', 'record-info', [
                ok({'status': 'PENDING'}),
                ok({'status': 'SUCCESS', 'response': {'sunoData': [
                    {'audioUrl': 'https://cdn/a.mp3'}, {'audioUrl': 'https://cdn/b.mp3?sig=1'}]}}),
            ]),
            ('GET', 'https://cdn/', FakeResponse(content=self.result_bytes)),
        ])
        with mock.patch.dict(os.environ, {'SUNO_API_KEY': 'k'}):
            code, stdout, err = run(['cover', str(self.song), '--style', 'citypop', '--start', '3',
                                     '--lyrics', str(self.lyrics), '--video', str(self.video),
                                     '--interval', '0', '--out', out], http)
        self.assertEqual(code, 0, err)
        (run_dir,) = made()
        for i in (1, 2):
            self.assertAlmostEqual(audio.duration(run_dir / f'final_{i}.mp3'), 5, delta=0.15)
            self.assertAlmostEqual(audio.duration(run_dir / f'final_{i}.mp4'), 5, delta=0.15)
            self.assertTrue((run_dir / f'raw_{i}.mp3').exists())
        meta = json.loads((run_dir / 'meta.json').read_text(encoding='utf-8'))
        self.assertEqual(meta['task'], 'task-123')
        self.assertEqual(meta['tracks'], ['https://cdn/a.mp3', 'https://cdn/b.mp3?sig=1'])
        self.assertEqual([Path(f).name for f in meta['files']],
                         ['final_1.mp3', 'final_1.mp4', 'final_2.mp3', 'final_2.mp4'])
        self.assertEqual(meta['payload']['uploadUrl'], 'https://tmp/source.mp3')
        self.assertIn('task-123', stdout)
        submit = next(kw for m, url, kw in http.calls if url.endswith('upload-cover'))
        self.assertEqual(submit['json']['prompt'], '[chorus]\n你问我爱你有多深')

    def test_no_wait_then_fetch_dir(self):
        out, made = self.out_dir()
        submit = FakeHTTP([
            ('POST', 'file-stream-upload', ok({'downloadUrl': 'https://tmp/source.mp3'})),
            ('POST', 'upload-cover', ok({'taskId': 'task-7'})),
        ])
        with mock.patch.dict(os.environ, {'SUNO_API_KEY': 'k'}):
            code, stdout, err = run(['cover', str(self.song), '--style', 'lofi', '--instrumental',
                                     '--video', str(self.video), '--no-wait', '--out', out], submit)
            self.assertEqual(code, 0, err)
            (run_dir,) = made()
            self.assertIn(f'fetch {run_dir}', stdout)
            self.assertFalse(any('record-info' in url for _, url, _ in submit.calls))

            fetch = FakeHTTP([
                ('GET', 'record-info', ok({'status': 'SUCCESS', 'response': {'sunoData': [
                    {'audioUrl': 'https://cdn/a.mp3'}]}})),
                ('GET', 'https://cdn/', FakeResponse(content=self.result_bytes)),
            ])
            code, _, err = run(['fetch', str(run_dir), '--interval', '0'], fetch)
        self.assertEqual(code, 0, err)
        self.assertEqual(fetch.calls[0][2]['params'], {'taskId': 'task-7'})
        # 结果放回原目录，并沿用提交时的视频
        self.assertAlmostEqual(audio.duration(run_dir / 'final_1.mp3'), 5, delta=0.15)
        self.assertAlmostEqual(audio.duration(run_dir / 'final_1.mp4'), 5, delta=0.15)
        self.assertEqual(made(), [run_dir])

    def test_fetch_dir_without_task(self):
        out, made = self.out_dir()
        run(['cover', str(self.song), '--duration', '5', '--dry-run', '--out', out])
        (run_dir,) = made()
        code, _, err = run(['fetch', str(run_dir)])
        self.assertEqual(code, 1)
        self.assertIn('没有已提交的任务', err)

    def test_check(self):
        with mock.patch.object(remix, 'load_toml', return_value={}):
            with mock.patch.dict(os.environ, {'SUNO_API_KEY': '', 'REPLICATE_API_TOKEN': ''}):
                code, stdout, _ = run(['check'])
            self.assertEqual(code, 1)
            self.assertIn('都没配置', stdout)
            with mock.patch.dict(os.environ, {'SUNO_API_KEY': 'k', 'REPLICATE_API_TOKEN': ''}):
                code, stdout, _ = run(['check'])
            self.assertEqual(code, 0, stdout)
            self.assertIn('环境正常', stdout)

    def test_fetch_minimax(self):
        out, made = self.out_dir()
        http = FakeHTTP([
            ('GET', '/predictions/p9', FakeResponse({'status': 'succeeded', 'output': 'https://replicate.delivery/x.mp3'})),
            ('GET', 'replicate.delivery', FakeResponse(content=self.result_bytes)),
        ])
        with mock.patch.dict(os.environ, {'REPLICATE_API_TOKEN': 't'}):
            code, _, err = run(['fetch', 'p9', '--provider', 'minimax', '--duration', '4',
                                '--format', 'm4a', '--out', out], http)
        self.assertEqual(code, 0, err)
        (run_dir,) = made()
        self.assertAlmostEqual(audio.duration(run_dir / 'final_1.m4a'), 4, delta=0.15)

    def test_finish(self):
        out, _ = self.out_dir()
        code, _, err = run(['finish', str(self.song), '--duration', '0:03', '--video', str(self.video),
                            '--keep-video-audio', '--out', out])
        self.assertEqual(code, 0, err)
        self.assertAlmostEqual(audio.duration(Path(out) / '老歌-final.mp3'), 3, delta=0.15)
        self.assertAlmostEqual(audio.duration(Path(out) / '老歌-final.mp4'), 5, delta=0.15)


if __name__ == '__main__':
    unittest.main()


def payload_of(stdout):
    return json.loads(stdout[stdout.index('{'):stdout.rindex('}') + 1])


@unittest.skipUnless(shutil.which('ffmpeg'), '需要 ffmpeg')
class ScoreCliTest(unittest.TestCase):
    """从曲谱出发：melody 命令，以及 cover 直接吃简谱 / MIDI。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.example = remix.HERE / 'examples' / '小星星.txt'
        cls.short = cls.dir / '短.txt'          # 2 小节，速度 120 → 4 秒
        cls.short.write_text('标题: 短歌\n速度: 120\n| 1 2 3 4 | 5 - - - |\n词: 一二三四五\n', encoding='utf-8')
        cls.bad = cls.dir / '错.txt'
        cls.bad.write_text('| 1 2 3 4 | 5 6 7 | 1 2 3 4 |\n词: 一二三\n', encoding='utf-8')
        cls.no_lyrics = cls.dir / '无词.txt'
        cls.no_lyrics.write_text('| 1 2 3 4 | 5 - - - |\n', encoding='utf-8')
        cls.midi = cls.dir / 'tune.mid'
        make_midi(cls.midi, [('Bass', 1, [(b, 1, 40) for b in range(8)]),
                             ('Lead', 0, [(b, 1, 72 + b % 3) for b in range(8)])])
        cls.video = cls.dir / 'v.mp4'
        make_video(cls.video, 10)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def out_dir(self):
        d = tempfile.mkdtemp(dir=self.dir)
        return d, lambda: list(Path(d).iterdir())

    def test_melody_check(self):
        code, stdout, _ = run(['melody', str(self.example), '--check'])
        self.assertEqual(code, 0)
        self.assertIn('《小星星》1=C 4/4 ♩=90，12 小节，约 0:32', stdout)
        self.assertIn('没发现问题', stdout)
        code, stdout, _ = run(['melody', str(self.bad), '--check'])
        self.assertEqual(code, 1)
        self.assertIn('⚠', stdout)
        self.assertIn('2 处要对照原谱核对', stdout)

    def test_melody_renders_demo_and_lyrics(self):
        out, made = self.out_dir()
        code, stdout, err = run(['melody', str(self.example), '--transpose', '2', '--out', out])
        self.assertEqual(code, 0, err)
        self.assertIn('变调 +2 个半音', stdout)
        (run_dir,) = made()
        self.assertAlmostEqual(audio.duration(run_dir / 'demo.wav'), 33, delta=0.1)
        self.assertTrue((run_dir / 'lyrics.txt').read_text(encoding='utf-8').startswith('[verse]\n一闪一闪亮晶晶'))

    def test_melody_lists_midi_parts(self):
        out, _ = self.out_dir()
        code, stdout, err = run(['melody', str(self.midi), '--out', out])
        self.assertEqual(code, 0, err)
        self.assertIn('2（Lead） 8 个音 ← 用这个', stdout)

    def test_melody_rejects_audio(self):
        song = self.dir / 'x.mp3'
        make_tone(song, 2)
        code, _, err = run(['melody', str(song)])
        self.assertEqual(code, 1)
        self.assertIn('只认曲谱', err)

    def test_cover_from_score_uses_its_lyrics(self):
        out, made = self.out_dir()
        code, stdout, err = run(['cover', str(self.example), '--style', 'lofi', '--model', 'V5',
                                 '--dry-run', '--out', out])
        self.assertEqual(code, 0, err)
        p = payload_of(stdout)
        self.assertEqual(p['prompt'].splitlines()[:2], ['[verse]', '一闪一闪亮晶晶'])
        self.assertEqual(p['audioWeight'], remix.SCORE_KEEP)
        self.assertEqual(p['title'], '小星星 (lofi)')
        (run_dir,) = made()
        self.assertTrue((run_dir / 'demo.wav').exists())
        self.assertAlmostEqual(audio.duration(run_dir / 'source.mp3'), 33, delta=0.15)

    def test_cover_keep_and_lyrics_options_override_score(self):
        lyrics = self.dir / '新词.txt'
        lyrics.write_text('新的歌词', encoding='utf-8')
        out, _ = self.out_dir()
        code, stdout, err = run(['cover', str(self.example), '--keep', '0.6', '--lyrics', str(lyrics),
                                 '--dry-run', '--out', out])
        self.assertEqual(code, 0, err)
        p = payload_of(stdout)
        self.assertEqual((p['audioWeight'], p['prompt']), (0.6, '新的歌词'))

    def test_short_score_repeats_to_fill_video(self):
        out, made = self.out_dir()
        code, stdout, err = run(['cover', str(self.short), '--video', str(self.video), '--dry-run', '--out', out])
        self.assertEqual(code, 0, err)
        self.assertIn('重复 3 遍', stdout)
        self.assertEqual(payload_of(stdout)['prompt'], '一二三四五\n一二三四五\n一二三四五')
        (run_dir,) = made()
        self.assertAlmostEqual(audio.duration(run_dir / 'demo.wav'), 13, delta=0.1)    # 3 × 4 秒 + 1 秒余音
        self.assertAlmostEqual(audio.duration(run_dir / 'source.mp3'), 12, delta=0.15)  # 视频 10 秒 + 2 秒

    def test_score_without_lyrics(self):
        out, made = self.out_dir()
        code, _, err = run(['cover', str(self.no_lyrics), '--dry-run', '--out', out])
        self.assertEqual(code, 1)
        self.assertIn('谱子里没有歌词', err)
        self.assertEqual(made(), [])
        code, stdout, err = run(['cover', str(self.no_lyrics), '--instrumental', '--dry-run', '--out', out])
        self.assertEqual(code, 0, err)
        self.assertTrue(payload_of(stdout)['instrumental'])

    def test_cover_from_midi_part(self):
        out, _ = self.out_dir()
        code, stdout, err = run(['cover', str(self.midi), '--part', '1', '--instrumental', '--no-beat',
                                 '--dry-run', '--out', out])
        self.assertEqual(code, 0, err)
        self.assertIn('1（Bass） 8 个音 ← 用这个', stdout)

    def test_bad_score_reports_line(self):
        broken = self.dir / '坏.txt'
        broken.write_text('| 1 2 3 4 |\n| 5 6 7 9 |\n', encoding='utf-8')
        code, _, err = run(['cover', str(broken), '--dry-run'])
        self.assertEqual(code, 1)
        self.assertIn('第 2 行：看不懂“9”', err)
