"""云端翻唱接口。每个接口负责三件事：把本地音频变成公网链接、提交翻唱任务、查询任务状态。

- suno：sunoapi.org 转接的 Suno 翻唱（Suno 官方没有开放 API）。接口格式相同的 kie.ai 等
  改 config.toml 里的 base / upload_base 即可。
- minimax：Replicate 上 MiniMax 的翻唱模型 minimax/music-cover。

第三方接口的字段可能会变：对不上时先用 --dry-run 看请求体，再用 --set 补字段，或者改这里。
http 参数是 requests.Session 一类的对象，测试时换成假的。
"""
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path


class RemixError(Exception):
    pass


@dataclass
class Job:
    style: str                       # 目标风格描述（英文效果更稳）
    title: str = 'Remix'
    lyrics: str | None = None        # 要唱的歌词；不填时 suno 走非自定义模式，minimax 沿用原曲歌词
    instrumental: bool = False       # 只要纯伴奏
    negative: str = ''               # 不想要的元素
    vocal: str | None = None         # 'f' 女声 / 'm' 男声
    keep: float | None = None        # 保留原曲的程度 0~1，越大越像原曲
    extra: dict = field(default_factory=dict)  # 原样并入请求体，应对接口新增的字段


def _json(resp):
    try:
        body = resp.json()
    except ValueError:
        raise RemixError(f'HTTP {resp.status_code}：{resp.text[:300]}') from None
    if resp.status_code >= 400:
        raise RemixError(f'HTTP {resp.status_code}：{body}')
    return body


class Suno:
    name = 'suno'
    min_seconds, max_seconds = 1, 480          # 上传的音频不能超过 8 分钟
    knobs = {'keep', 'negative', 'vocal', 'instrumental'}

    def __init__(self, http, key, base='https://api.sunoapi.org',
                 upload_base='https://sunoapiorg.redpandaai.co', model='V5',
                 callback='https://example.com/callback'):
        # callBackUrl 是必填项；这里靠轮询拿结果，回调地址随便填一个能访问的即可
        self.http, self.base, self.upload_base = http, base.rstrip('/'), upload_base.rstrip('/')
        self.model, self.callback = model, callback
        self.headers = {'Authorization': f'Bearer {key}'}
        self.upload_note = f'上传到 {self.upload_base}（服务商的临时文件区）'

    def _ok(self, resp):
        body = _json(resp)
        if body.get('code') != 200:
            raise RemixError(f"suno 接口报错 {body.get('code')}：{body.get('msg')}")
        return body.get('data') or {}

    def upload(self, path):
        """用 sunoapi 自带的文件上传拿公网链接（临时文件，过几天自动删除）。"""
        name = f'{uuid.uuid4().hex}{Path(path).suffix}'
        with open(path, 'rb') as f:
            r = self.http.post(f'{self.upload_base}/api/file-stream-upload', headers=self.headers,
                               data={'uploadPath': 'song-remix', 'fileName': name},
                               files={'file': (name, f)}, timeout=300)
        data = self._ok(r)
        url = data.get('downloadUrl') or data.get('fileUrl')
        if not url:
            raise RemixError(f'上传成功但返回里没有文件链接：{data}')
        return url

    def payload(self, audio_url, job):
        p = {'uploadUrl': audio_url, 'model': self.model, 'callBackUrl': self.callback,
             'instrumental': job.instrumental}
        if job.lyrics or job.instrumental:
            # 自定义模式：style 定风格，prompt 就是要唱的歌词
            p.update(customMode=True, style=job.style[:1000], title=job.title[:80])
            if not job.instrumental:
                p['prompt'] = job.lyrics[:5000]
        else:
            # 非自定义模式：只给一段描述，歌词交给 Suno 处理
            p.update(customMode=False, prompt=job.style[:500])
        optional = {'negativeTags': job.negative, 'vocalGender': job.vocal, 'audioWeight': job.keep}
        p.update({k: v for k, v in optional.items() if v not in (None, '')})
        p.update(job.extra)
        return p

    def submit(self, payload):
        r = self.http.post(f'{self.base}/api/v1/generate/upload-cover', headers=self.headers,
                           json=payload, timeout=60)
        task = self._ok(r).get('taskId')
        if not task:
            raise RemixError('提交成功但没拿到 taskId')
        return task

    def poll(self, task):
        """返回 (状态, 音频链接, 错误信息)，状态是 'done' / 'failed' / 'running'。"""
        r = self.http.get(f'{self.base}/api/v1/generate/record-info', headers=self.headers,
                          params={'taskId': task}, timeout=30)
        data = self._ok(r)
        status = str(data.get('status') or '')
        tracks = (data.get('response') or {}).get('sunoData') or []
        urls = [u for t in tracks if (u := t.get('audioUrl') or t.get('sourceAudioUrl'))]
        if status == 'SUCCESS':
            return 'done', urls, None
        if any(w in status for w in ('FAIL', 'ERROR', 'EXCEPTION')):
            return 'failed', urls, data.get('errorMessage') or status
        return 'running', urls, None


class MiniMax:
    name = 'minimax'
    min_seconds, max_seconds = 6, 360          # 参考音频 6 秒 ~ 6 分钟
    knobs = {'vocal'}
    VOCALS = {'f': 'female vocals', 'm': 'male vocals'}

    def __init__(self, http, token, model='minimax/music-cover',
                 base='https://api.replicate.com/v1', upload_expire='1h'):
        self.http, self.model, self.base = http, model, base.rstrip('/')
        self.upload_expire = upload_expire
        self.headers = {'Authorization': f'Bearer {token}'}
        self.upload_note = f'上传到 litterbox.catbox.moe（公开链接，{upload_expire} 后自动删除）'

    def upload(self, path):
        # Replicate 这个模型只收公网链接，借用匿名临时文件站
        return litterbox(self.http, path, self.upload_expire)

    def payload(self, audio_url, job):
        if job.instrumental:
            raise RemixError('minimax 不支持纯伴奏，要纯音乐请用 --provider suno')
        prompt = job.style
        if job.vocal in self.VOCALS:
            prompt += ', ' + self.VOCALS[job.vocal]
        inp = {'audio_url': audio_url, 'prompt': prompt[:2000], 'audio_format': 'mp3'}
        if job.lyrics:
            inp['lyrics'] = job.lyrics[:3000]
        inp.update(job.extra)
        return {'input': inp}

    def submit(self, payload):
        r = self.http.post(f'{self.base}/models/{self.model}/predictions', headers=self.headers,
                           json=payload, timeout=60)
        body = _json(r)
        if not body.get('id'):
            raise RemixError(f'提交后没拿到任务 ID：{body}')
        return body['id']

    def poll(self, task):
        body = _json(self.http.get(f'{self.base}/predictions/{task}', headers=self.headers, timeout=30))
        status, out = body.get('status'), body.get('output')
        urls = [out] if isinstance(out, str) else list(out or [])
        if status == 'succeeded':
            return 'done', urls, None
        if status in ('failed', 'canceled'):
            return 'failed', urls, body.get('error') or status
        return 'running', urls, None


def litterbox(http, path, expire='1h'):
    """匿名临时文件站 litterbox.catbox.moe：链接公开，到期自动删除（1h / 12h / 24h / 72h）。"""
    with open(path, 'rb') as f:
        r = http.post('https://litterbox.catbox.moe/resources/internals/api.php',
                      data={'reqtype': 'fileupload', 'time': expire},
                      files={'fileToUpload': (Path(path).name, f)}, timeout=300)
    url = r.text.strip()
    if r.status_code >= 400 or not url.startswith('http'):
        raise RemixError(f'litterbox 上传失败：{url[:300]}')
    return url


def wait(provider, task, interval=10, timeout=900, sleep=time.sleep, clock=time.monotonic, log=print):
    """轮询到任务结束，返回音频链接。超时时如果已有部分结果就先返回。"""
    end = clock() + timeout
    while True:
        state, urls, err = provider.poll(task)
        if state == 'done':
            if not urls:
                raise RemixError('任务完成了，但返回里没有音频链接')
            return urls
        if state == 'failed':
            raise RemixError(f'生成失败：{err}')
        if clock() >= end:
            if urls:
                log('等待超时，先取已经生成好的部分')
                return urls
            raise RemixError(f'等了 {timeout} 秒还没完成，稍后可以用 fetch {task} 再取')
        sleep(interval)


def download(http, url, dst):
    with http.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(dst, 'wb') as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
