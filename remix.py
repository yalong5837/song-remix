#!/usr/bin/env python3
"""
老歌改编（风格翻唱）：截一段老歌，交给云端模型换个风格重新演绎，再处理成能直接放进短视频的配乐。

  python3 remix.py styles                                    看有哪些风格预设
  python3 remix.py cover 老歌.mp3 --style citypop --start 1:05 --duration 30 --lyrics 副歌.txt
  python3 remix.py cover 老歌.mp3 --style lofi --instrumental --start 0:48 --video 视频.mp4
  python3 remix.py fetch <任务ID> --provider suno             等待超时后再去取结果
  python3 remix.py finish out/…/raw_1.mp3 --video 视频.mp4     重新裁剪 / 合成，不再调用接口

详细说明见 README.md。
"""
import argparse
import json
import os
import sys
import time
import tomllib
from pathlib import Path

from remixer import audio
from remixer.providers import Job, MiniMax, RemixError, Suno, download, wait

HERE = Path(__file__).resolve().parent
PLACEHOLDER = '<上传后的公网链接>'
AUDIO_EXTS = ('.mp3', '.wav', '.m4a', '.flac')


def load_toml(path):
    if not path.exists():
        return {}
    with open(path, 'rb') as f:
        return tomllib.load(f)


def make_http():
    import requests
    s = requests.Session()
    s.headers['User-Agent'] = 'song-remix/0.1'
    return s


def make_provider(args, cfg, http, need_key=True):
    c = cfg.get(args.provider, {})
    if args.provider == 'suno':
        cls, key, env = Suno, os.environ.get('SUNO_API_KEY') or c.get('key'), 'SUNO_API_KEY'
        opts = {k: c[k] for k in ('base', 'upload_base', 'model', 'callback') if k in c}
    else:
        cls, key, env = MiniMax, os.environ.get('REPLICATE_API_TOKEN') or c.get('token'), 'REPLICATE_API_TOKEN'
        opts = {k: c[k] for k in ('model', 'upload_expire') if k in c}
    if args.model:
        opts['model'] = args.model
    if need_key and not key:
        raise RemixError(f'缺少 {args.provider} 的密钥：设置环境变量 {env}，或者写进 config.toml')
    return cls(http, key or '', **opts)


def parse_sets(items):
    extra = {}
    for item in items or []:
        k, sep, v = item.partition('=')
        if not sep:
            raise RemixError(f'--set 要写成 键=值：{item}')
        try:
            extra[k] = json.loads(v)       # 数字、true/false 按原类型传
        except ValueError:
            extra[k] = v
    return extra


def build_job(args, presets, stem):
    preset = presets.get(args.style, {})
    style = preset.get('style', args.style)
    if args.more:
        style += ', ' + args.more
    lyrics = Path(args.lyrics).read_text(encoding='utf-8').strip() if args.lyrics else None
    keep = args.keep if args.keep is not None else preset.get('keep')
    if keep is not None and not 0 <= keep <= 1:
        raise RemixError(f'--keep 要在 0~1 之间，现在是 {keep}')
    return Job(style=style, title=args.title or f'{stem} ({args.style if preset else "remix"})',
               lyrics=lyrics, instrumental=args.instrumental,
               negative=args.negative or preset.get('negative', ''),
               vocal=args.vocal or preset.get('vocal'),
               keep=keep, extra=parse_sets(args.set))


def new_out_dir(base, label):
    label = ''.join(c if c.isalnum() or c in '-_' else '_' for c in label)[:40]
    d = Path(base) / f"{time.strftime('%Y%m%d-%H%M%S')}-{label}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_meta(out, **info):
    path = out / 'meta.json'
    meta = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    meta.update(info)
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')


def target_length(args):
    """成品时长：--duration 和视频长度取短的（淡出才落在视频里），都没给就不裁。"""
    lengths = [x for x in (audio.parse_time(args.duration), args.video and audio.duration(args.video)) if x]
    return min(lengths) if lengths else None


def make_final(src, final, args, length):
    """一个音频 → 配乐成品，给了 --video 再合成成片。"""
    sec = audio.finish(src, final, length, args.fade_in, args.fade_out, args.lufs)
    print(f'配乐：{final}（{audio.fmt_time(sec)}）')
    if args.video:
        vol = args.music_volume if args.music_volume is not None else (0.35 if args.keep_video_audio else 1.0)
        mp4 = final.with_suffix('.mp4')
        audio.mux(args.video, final, mp4, vol, args.keep_video_audio)
        print(f'成片：{mp4}')


def deliver(http, urls, out, args, length):
    for i, url in enumerate(urls, 1):
        ext = Path(url.split('?')[0]).suffix.lower()
        raw = out / f'raw_{i}{ext if ext in AUDIO_EXTS else ".mp3"}'
        download(http, url, raw)
        print(f'—— 版本 {i}')
        make_final(raw, out / f'final_{i}.{args.format}', args, length)


def cmd_cover(args, cfg, presets, http):
    src = Path(args.src)
    if not src.exists():
        raise RemixError(f'找不到原曲：{src}')
    job = build_job(args, presets, src.stem)
    provider = make_provider(args, cfg, http, need_key=not args.dry_run)
    preview = provider.payload(args.audio_url or PLACEHOLDER, job)   # 先检查参数，出错就不必截取上传
    for knob, given in (('keep', args.keep is not None), ('negative', bool(args.negative))):
        if given and knob not in provider.knobs:
            print(f'提示：{provider.name} 不支持 --{knob}，已忽略')

    target = target_length(args)
    start = audio.parse_time(args.start) or 0.0
    # 没给 --duration 但给了视频：按视频长度多截 2 秒，给结尾淡出留余量
    length = audio.parse_time(args.duration) or (target + 2 if target else None)
    out = new_out_dir(args.out, f'{src.stem}-{args.style if args.style in presets else "custom"}')
    clip_path = out / 'source.mp3'
    sec = audio.clip(src, clip_path, start, length)
    print(f'截取 {audio.fmt_time(start)}–{audio.fmt_time(start + sec)}（{sec:.1f} 秒）→ {clip_path}')
    if not provider.min_seconds <= sec <= provider.max_seconds:
        raise RemixError(f'{provider.name} 要求片段在 {provider.min_seconds}~{provider.max_seconds} 秒之间，'
                         f'现在是 {sec:.1f} 秒，用 --start / --duration 调整')
    if args.dry_run:
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        print('（dry-run：没有上传，也没有调用接口）')
        return

    if args.audio_url:
        url = args.audio_url
    else:
        print(provider.upload_note + ' ……')
        url = provider.upload(clip_path)
    payload = provider.payload(url, job)
    task = provider.submit(payload)
    save_meta(out, provider=provider.name, task=task, source=str(src), start=start, seconds=sec, payload=payload)
    print(f'已提交，任务 {task}，等待生成（一般 1~5 分钟）……')
    urls = wait(provider, task, interval=args.interval, timeout=args.timeout)
    save_meta(out, tracks=urls)
    deliver(http, urls, out, args, target)
    print(f'全部文件在 {out}')


def cmd_fetch(args, cfg, http):
    provider = make_provider(args, cfg, http)
    urls = wait(provider, args.task, interval=args.interval, timeout=args.timeout)
    out = new_out_dir(args.out, f'fetch-{args.task[:8]}')
    save_meta(out, provider=provider.name, task=args.task, tracks=urls)
    deliver(http, urls, out, args, target_length(args))
    print(f'全部文件在 {out}')


def cmd_finish(args):
    src = Path(args.audio)
    out = Path(args.out) if args.out else src.parent
    out.mkdir(parents=True, exist_ok=True)
    make_final(src, out / f'{src.stem}-final.{args.format}', args, target_length(args))


def cmd_styles(presets):
    for name, p in presets.items():
        keep = f"，keep {p['keep']}" if 'keep' in p else ''
        print(f"{name:<10} {p.get('desc', '')}{keep}\n{'':<10} {p['style']}")


def build_parser():
    ap = argparse.ArgumentParser(description='老歌改编：风格翻唱 + 短视频配乐。详细说明见 README.md。')
    sub = ap.add_subparsers(dest='cmd', required=True)

    post = argparse.ArgumentParser(add_help=False)
    g = post.add_argument_group('成品处理')
    g.add_argument('--video', help='短视频文件：配乐裁成和视频一样长，并合成 final_N.mp4')
    g.add_argument('--keep-video-audio', action='store_true', help='合成时保留视频原声（比如口播），配乐垫在下面')
    g.add_argument('--music-volume', type=float, help='配乐音量倍数（默认：替换原声 1.0，保留原声 0.35）')
    g.add_argument('--fade-in', type=float, default=0.0, help='淡入秒数（默认 0）')
    g.add_argument('--fade-out', type=float, default=1.5, help='淡出秒数（默认 1.5）')
    g.add_argument('--lufs', type=float, default=-14.0, help='响度目标（默认 -14 LUFS）')
    g.add_argument('--format', choices=['mp3', 'm4a', 'wav'], default='mp3', help='配乐格式（默认 mp3）')

    net = argparse.ArgumentParser(add_help=False)
    g = net.add_argument_group('接口')
    g.add_argument('--provider', choices=['suno', 'minimax'], default='suno',
                   help='suno：sunoapi.org 转接的 Suno（默认）；minimax：Replicate 上的 MiniMax 翻唱')
    g.add_argument('--model', help='模型版本，比如 suno 的 V5（默认看 config.toml）')
    g.add_argument('--interval', type=float, default=10, help='查询间隔秒数（默认 10）')
    g.add_argument('--timeout', type=float, default=900, help='最多等多少秒（默认 900）')
    g.add_argument('--out', default='out', help='输出目录（默认 ./out）')

    c = sub.add_parser('cover', parents=[net, post], allow_abbrev=False, help='截一段老歌，换风格翻唱')
    c.add_argument('src', help='原曲音频（mp3 / wav / m4a / flac 都行）')
    c.add_argument('--style', default='citypop', help='风格预设名（见 styles 命令），或者直接写一段风格描述')
    c.add_argument('--more', help='追加在风格后面的描述，比如 "slower, female vocals"')
    c.add_argument('--start', help='从哪里开始截，比如 1:05（默认从头）')
    c.add_argument('--duration', help='截多长，也是成品时长，比如 30 或 0:30（默认：有 --video 按视频长度，否则整首）')
    c.add_argument('--lyrics', help='这段要唱的歌词（txt 文件）。suno 强烈建议提供；minimax 不给就沿用原歌词')
    c.add_argument('--instrumental', action='store_true', help='只要纯伴奏（只有 suno 支持）')
    c.add_argument('--keep', type=float, help='保留原曲的程度 0~1，越大越像原曲（只对 suno 生效，默认看预设）')
    c.add_argument('--vocal', choices=['f', 'm'], help='女声 f / 男声 m')
    c.add_argument('--negative', help='不想要的元素，比如 "autotune, heavy distortion"（只对 suno 生效）')
    c.add_argument('--title', help='新歌名（默认“原名 (风格)”）')
    c.add_argument('--audio-url', help='跳过上传，直接用这个公网链接（应指向截好的片段）')
    c.add_argument('--set', action='append', metavar='键=值', help='往请求体里加 / 改字段，可多次使用')
    c.add_argument('--dry-run', action='store_true', help='只截片段、打印请求体，不上传、不花钱')

    f = sub.add_parser('fetch', parents=[net, post], allow_abbrev=False, help='按任务 ID 取回结果（等待超时后用）')
    f.add_argument('task', help='cover 命令打印的任务 ID（也记在 meta.json 里）')
    f.add_argument('--duration', help='成品时长（默认不裁；有 --video 按视频长度）')

    fin = sub.add_parser('finish', parents=[post], allow_abbrev=False, help='重新处理已下载的音频，不调用接口')
    fin.add_argument('audio', help='音频文件，比如 out/…/raw_1.mp3')
    fin.add_argument('--duration', help='成品时长（默认不裁；有 --video 按视频长度）')
    fin.add_argument('--out', help='输出目录（默认和音频放在一起）')

    sub.add_parser('styles', help='列出风格预设')
    return ap


def main(argv=None, http=None):
    args = build_parser().parse_args(argv)
    try:
        presets = load_toml(HERE / 'presets.toml')
        cfg = load_toml(HERE / 'config.toml')
        if args.cmd == 'styles':
            cmd_styles(presets)
        elif args.cmd == 'finish':
            cmd_finish(args)
        elif args.cmd == 'cover':
            cmd_cover(args, cfg, presets, http or make_http())
        else:
            cmd_fetch(args, cfg, http or make_http())
    except (RemixError, ValueError, RuntimeError, OSError) as e:
        print(f'出错：{e}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
