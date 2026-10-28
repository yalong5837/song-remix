"""ffmpeg 小工具：截取片段、处理成品（裁剪、淡入淡出、响度标准化）、把配乐合进视频。"""
import json
import shutil
import subprocess
from pathlib import Path

CODECS = {
    '.mp3': ['-c:a', 'libmp3lame', '-b:a', '320k'],
    '.m4a': ['-c:a', 'aac', '-b:a', '256k'],
    '.wav': ['-c:a', 'pcm_s16le'],
}


def parse_time(text):
    """'1:05' → 65.0，'1:02:03' → 3723.0，'90' / '90.5' → 秒数；None 原样返回。"""
    if text is None:
        return None
    sec = 0.0
    try:
        for part in str(text).strip().split(':'):
            sec = sec * 60 + float(part)
    except ValueError:
        raise ValueError(f'时间格式不对：{text}（可以写 1:05 或 65）') from None
    if sec < 0:
        raise ValueError(f'时间不能是负数：{text}')
    return sec


def fmt_time(sec):
    """65.4 → '1:05'。"""
    sec = int(round(sec))
    return f'{sec // 60}:{sec % 60:02d}'


def _run(args):
    r = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', *map(str, args)],
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError('ffmpeg 失败：' + r.stderr.strip())


def _probe(path, *args):
    r = subprocess.run(['ffprobe', '-v', 'error', *args, '-of', 'json', str(path)],
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f'读不了 {path}：{r.stderr.strip()}')
    return json.loads(r.stdout)


def duration(path):
    return float(_probe(path, '-show_entries', 'format=duration')['format']['duration'])


def has_audio(path):
    return bool(_probe(path, '-select_streams', 'a', '-show_entries', 'stream=index').get('streams'))


def check():
    """返回 ffmpeg 相关的问题清单，空列表表示没问题。"""
    missing = [t for t in ('ffmpeg', 'ffprobe') if not shutil.which(t)]
    if missing:
        return [f'没装 {"、".join(missing)}：Debian/Ubuntu 用 apt install ffmpeg，macOS 用 brew install ffmpeg']
    r = subprocess.run(['ffmpeg', '-hide_banner', '-encoders'], capture_output=True, text=True)
    return [] if 'libmp3lame' in r.stdout else ['ffmpeg 没带 mp3 编码器 libmp3lame，换一个完整版的 ffmpeg']


def _codec(dst):
    ext = Path(dst).suffix.lower()
    if ext not in CODECS:
        raise ValueError(f'不支持的输出格式 {ext}，可选：{" ".join(CODECS)}')
    return CODECS[ext]


def clip(src, dst, start=0.0, length=None, fade=0.05):
    """截取 [start, start+length]（length 为 None 时截到结尾），统一转成 44.1kHz 立体声。
    两端加很短的淡入淡出，避免切口的“咔哒”声。返回实际截取的秒数。"""
    total = duration(src)
    if start >= total:
        raise ValueError(f'起点 {fmt_time(start)} 超出了歌曲长度 {fmt_time(total)}')
    length = min(length or total - start, total - start)
    af = f'afade=t=in:d={fade},afade=t=out:st={max(0.0, length - fade):.3f}:d={fade}'
    _run(['-ss', f'{start:.3f}', '-t', f'{length:.3f}', '-i', src, '-vn', '-af', af,
          '-ar', 44100, '-ac', 2, *_codec(dst), dst])
    return length


def finish(src, dst, length=None, fade_in=0.0, fade_out=1.5, lufs=-14.0):
    """把生成结果处理成能直接用的配乐：裁到 length 秒、淡入淡出、响度标准化到 lufs
    （短视频平台普遍按 -14 LUFS 左右播放）。返回成品秒数。"""
    total = duration(src)
    length = min(length or total, total)
    filters = []
    if fade_in > 0:
        filters.append(f'afade=t=in:d={fade_in}')
    if fade_out > 0:
        fade_out = min(fade_out, length)
        filters.append(f'afade=t=out:st={length - fade_out:.3f}:d={fade_out:.3f}')
    filters.append(f'loudnorm=I={lufs}:TP=-1.5:LRA=11')
    _run(['-i', src, '-t', f'{length:.3f}', '-vn', '-af', ','.join(filters),
          '-ar', 44100, *_codec(dst), dst])
    return length


def mux(video, music, dst, music_volume=1.0, keep_video_audio=False):
    """把配乐放进视频，画面不重新编码。默认替换原声；keep_video_audio 时保留原声（比如口播），
    配乐按 music_volume 垫在下面。成片和视频一样长，配乐不够长就补静音。"""
    graph = f'[1:a]volume={music_volume},apad[m]'
    if keep_video_audio and has_audio(video):
        graph += ';[0:a][m]amix=inputs=2:duration=longest:normalize=0[a]'
    else:
        graph = graph.replace('[m]', '[a]')
    _run(['-i', video, '-i', music, '-filter_complex', graph, '-map', '0:v', '-map', '[a]',
          '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-t', f'{duration(video):.3f}', dst])
