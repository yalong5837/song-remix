import tempfile
import unittest
from pathlib import Path

from fakes import FakeHTTP, FakeResponse

from remixer.providers import Job, MiniMax, RemixError, Suno, litterbox, wait


def ok(data):
    return FakeResponse({'code': 200, 'msg': 'success', 'data': data})


class SunoTest(unittest.TestCase):
    def suno(self, *routes):
        http = FakeHTTP(routes)
        return Suno(http, 'KEY', model='V5'), http

    def test_lyrics_use_custom_mode(self):
        job = Job(style='city pop', title='x' * 100, lyrics='月亮代表我的心', keep=0.65,
                  extra={'styleWeight': 0.7})
        p = self.suno()[0].payload('https://a/b.mp3', job)
        self.assertEqual(p['uploadUrl'], 'https://a/b.mp3')
        self.assertTrue(p['customMode'])
        self.assertEqual(p['prompt'], '月亮代表我的心')
        self.assertEqual(p['style'], 'city pop')
        self.assertEqual(len(p['title']), 80)
        self.assertEqual(p['audioWeight'], 0.65)
        self.assertEqual(p['styleWeight'], 0.7)
        self.assertFalse(p['instrumental'])
        self.assertNotIn('negativeTags', p)
        self.assertNotIn('vocalGender', p)

    def test_instrumental_has_no_lyrics(self):
        p = self.suno()[0].payload('u', Job(style='lofi', instrumental=True, vocal='f', negative='drums'))
        self.assertTrue(p['customMode'])
        self.assertTrue(p['instrumental'])
        self.assertNotIn('prompt', p)
        self.assertEqual(p['negativeTags'], 'drums')

    def test_no_lyrics_uses_simple_mode(self):
        p = self.suno()[0].payload('u', Job(style='s' * 600))
        self.assertFalse(p['customMode'])
        self.assertEqual(len(p['prompt']), 500)
        self.assertNotIn('style', p)

    def test_submit_and_auth(self):
        suno, http = self.suno(('POST', '/api/v1/generate/upload-cover', ok({'taskId': 't1'})))
        self.assertEqual(suno.submit({'a': 1}), 't1')
        method, url, kw = http.calls[0]
        self.assertEqual(url, 'https://api.sunoapi.org/api/v1/generate/upload-cover')
        self.assertEqual(kw['headers']['Authorization'], 'Bearer KEY')
        self.assertEqual(kw['json'], {'a': 1})

    def test_api_error_code_raises(self):
        suno, _ = self.suno(('POST', 'upload-cover', FakeResponse({'code': 429, 'msg': '余额不足'})))
        with self.assertRaisesRegex(RemixError, '余额不足'):
            suno.submit({})

    def test_non_json_error(self):
        suno, _ = self.suno(('POST', 'upload-cover', FakeResponse('Bad Gateway', status=502)))
        with self.assertRaisesRegex(RemixError, '502'):
            suno.submit({})

    def test_poll_states(self):
        tracks = [{'audioUrl': 'https://cdn/1.mp3'}, {'audioUrl': '', 'sourceAudioUrl': 'https://cdn/2.mp3'}]
        suno, http = self.suno(('GET', 'record-info', [
            ok({'status': 'PENDING', 'response': None}),
            ok({'status': 'FIRST_SUCCESS', 'response': {'sunoData': tracks[:1]}}),
            ok({'status': 'SUCCESS', 'response': {'sunoData': tracks}}),
            ok({'status': 'SENSITIVE_WORD_ERROR', 'errorMessage': '歌词含敏感词'}),
        ]))
        self.assertEqual(suno.poll('t1'), ('running', [], None))
        self.assertEqual(suno.poll('t1'), ('running', ['https://cdn/1.mp3'], None))
        self.assertEqual(suno.poll('t1'), ('done', ['https://cdn/1.mp3', 'https://cdn/2.mp3'], None))
        self.assertEqual(suno.poll('t1'), ('failed', [], '歌词含敏感词'))
        self.assertEqual(http.calls[0][2]['params'], {'taskId': 't1'})

    def test_upload(self):
        suno, http = self.suno(('POST', 'file-stream-upload', ok({'downloadUrl': 'https://tmp/f.mp3'})))
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / 'source.mp3'
            src.write_bytes(b'x' * 1000)
            self.assertEqual(suno.upload(src), 'https://tmp/f.mp3')
        _, url, kw = http.calls[0]
        self.assertEqual(url, 'https://sunoapiorg.redpandaai.co/api/file-stream-upload')
        self.assertEqual(kw['data']['uploadPath'], 'song-remix')
        self.assertTrue(kw['data']['fileName'].endswith('.mp3'))
        self.assertEqual(kw['files']['file'][1], 1000)


class MiniMaxTest(unittest.TestCase):
    def test_payload(self):
        mm = MiniMax(FakeHTTP(), 'TOKEN')
        p = mm.payload('https://a/b.mp3', Job(style='R&B', vocal='f', keep=0.9))
        self.assertEqual(p, {'input': {'audio_url': 'https://a/b.mp3', 'prompt': 'R&B, female vocals',
                                       'audio_format': 'mp3'}})
        p = mm.payload('u', Job(style='rock', lyrics='[chorus]\n新词'))
        self.assertEqual(p['input']['lyrics'], '[chorus]\n新词')

    def test_instrumental_rejected(self):
        with self.assertRaisesRegex(RemixError, '纯伴奏'):
            MiniMax(FakeHTTP(), 'T').payload('u', Job(style='x', instrumental=True))

    def test_submit_and_poll(self):
        http = FakeHTTP([
            ('POST', '/models/minimax/music-cover/predictions', FakeResponse({'id': 'p1', 'status': 'starting'})),
            ('GET', '/predictions/p1', [
                FakeResponse({'status': 'processing', 'output': None}),
                FakeResponse({'status': 'succeeded', 'output': 'https://replicate.delivery/o.mp3'}),
                FakeResponse({'status': 'failed', 'error': 'audio too short'}),
            ]),
        ])
        mm = MiniMax(http, 'TOKEN')
        self.assertEqual(mm.submit({'input': {}}), 'p1')
        self.assertEqual(http.calls[0][2]['headers']['Authorization'], 'Bearer TOKEN')
        self.assertEqual(mm.poll('p1'), ('running', [], None))
        self.assertEqual(mm.poll('p1'), ('done', ['https://replicate.delivery/o.mp3'], None))
        self.assertEqual(mm.poll('p1'), ('failed', [], 'audio too short'))

    def test_http_error(self):
        http = FakeHTTP([('POST', 'predictions', FakeResponse({'detail': 'Unauthenticated'}, status=401))])
        with self.assertRaisesRegex(RemixError, '401'):
            MiniMax(http, 'bad').submit({})


class LitterboxTest(unittest.TestCase):
    def test_upload(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / 'a.mp3'
            src.write_bytes(b'abc')
            http = FakeHTTP([('POST', 'litterbox', FakeResponse('https://litter.catbox.moe/xyz.mp3\n'))])
            self.assertEqual(litterbox(http, src, '12h'), 'https://litter.catbox.moe/xyz.mp3')
            self.assertEqual(http.calls[0][2]['data'], {'reqtype': 'fileupload', 'time': '12h'})
            http = FakeHTTP([('POST', 'litterbox', FakeResponse('File too large', status=413))])
            with self.assertRaisesRegex(RemixError, 'File too large'):
                litterbox(http, src)


class Scripted:
    """按顺序返回预设状态的假接口。"""
    def __init__(self, *states):
        self.states = list(states)

    def poll(self, task):
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]


class WaitTest(unittest.TestCase):
    def run_wait(self, provider, timeout=100):
        now, sleeps = [0.0], []

        def sleep(s):
            sleeps.append(s)
            now[0] += s
        return wait(provider, 't1', interval=10, timeout=timeout, sleep=sleep,
                    clock=lambda: now[0], log=lambda *_: None), sleeps

    def test_waits_until_done(self):
        urls, sleeps = self.run_wait(Scripted(('running', [], None), ('running', ['a'], None),
                                              ('done', ['a', 'b'], None)))
        self.assertEqual(urls, ['a', 'b'])
        self.assertEqual(sleeps, [10, 10])

    def test_failure(self):
        with self.assertRaisesRegex(RemixError, '敏感词'):
            self.run_wait(Scripted(('failed', [], '敏感词')))

    def test_done_without_urls(self):
        with self.assertRaisesRegex(RemixError, '没有音频链接'):
            self.run_wait(Scripted(('done', [], None)))

    def test_timeout_returns_partial(self):
        urls, _ = self.run_wait(Scripted(('running', ['a'], None)), timeout=25)
        self.assertEqual(urls, ['a'])

    def test_timeout_suggests_fetch(self):
        with self.assertRaisesRegex(RemixError, 'fetch t1'):
            self.run_wait(Scripted(('running', [], None)), timeout=25)


if __name__ == '__main__':
    unittest.main()
