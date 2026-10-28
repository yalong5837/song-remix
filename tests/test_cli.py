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

from fakes import FakeHTTP, FakeResponse, make_tone, make_video

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
