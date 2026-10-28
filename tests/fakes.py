"""测试用的假 HTTP：按（方法, 网址片段）匹配预设的回复，并记下每次请求。"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class FakeResponse:
    def __init__(self, body=None, status=200, content=b''):
        self.status_code, self._body, self.content = status, body, content
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        if isinstance(self._body, (dict, list)):
            return self._body
        raise ValueError('不是 JSON')

    def raise_for_status(self):
        if self.status_code >= 400:
            raise OSError(f'HTTP {self.status_code}')

    def iter_content(self, size):
        for i in range(0, len(self.content), size):
            yield self.content[i:i + size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeHTTP:
    def __init__(self, routes=()):
        # routes：[(方法, 网址片段, 回复 或 回复列表)]，列表按顺序取，取到最后一个就一直用它
        self.routes, self.calls = list(routes), []

    def _handle(self, method, url, kw):
        self.calls.append((method, url, kw))
        for m, part, resp in self.routes:
            if m == method and part in url:
                return (resp.pop(0) if len(resp) > 1 else resp[0]) if isinstance(resp, list) else resp
        raise AssertionError(f'没预料到的请求：{method} {url}')

    def get(self, url, **kw):
        return self._handle('GET', url, kw)

    def post(self, url, **kw):
        if 'files' in kw:   # 像真的上传一样把文件读完，记下字节数
            kw['files'] = {k: (name, len(f.read())) for k, (name, f) in kw['files'].items()}
        return self._handle('POST', url, kw)


def ffmpeg(*args):
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', *map(str, args)], check=True)


def make_tone(path, seconds, freq=440):
    ffmpeg('-f', 'lavfi', '-i', f'sine=frequency={freq}:duration={seconds}', '-ac', 2, path)


def make_video(path, seconds, with_audio=True):
    args = ['-f', 'lavfi', '-i', f'testsrc=size=320x240:rate=25:duration={seconds}']
    if with_audio:
        args += ['-f', 'lavfi', '-i', f'sine=frequency=220:duration={seconds}', '-c:a', 'aac']
    ffmpeg(*args, '-c:v', 'libx264', '-pix_fmt', 'yuv420p', path)
