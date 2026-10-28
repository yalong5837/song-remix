# 老歌改编：风格翻唱 + 短视频配乐

截一段老歌，交给云端模型换个风格重新演绎（旋律和歌词保留），再自动裁成视频长度、加淡出、统一响度，可以直接合成进短视频。

```
老歌.mp3 ──截取片段──▶ 云端翻唱（Suno / MiniMax）──▶ 下载 2 个版本 ──▶ 裁剪 · 淡出 · -14 LUFS ──▶ final_N.mp3 / final_N.mp4
```

## 准备

1. Python 3.11+，ffmpeg（要带 libmp3lame，常见发行版自带）
2. `pip install -r requirements.txt`
3. 选一个接口，配好密钥（环境变量，或者把 `config.example.toml` 复制成 `config.toml` 再填）：

| | `suno`（默认） | `minimax` |
| --- | --- | --- |
| 来源 | [sunoapi.org](https://sunoapi.org) 转接的 Suno（Suno 官方没有开放 API） | [Replicate](https://replicate.com/minimax/music-cover) 上 MiniMax 的翻唱模型 |
| 密钥 | `SUNO_API_KEY` | `REPLICATE_API_TOKEN` |
| 一次出几版 | 通常 2 版 | 1 版 |
| 纯伴奏 `--instrumental` | 支持 | 不支持 |
| 保留度 `--keep` | 支持 | 不支持 |
| 歌词 | 建议用 `--lyrics` 给出；不给时走简单模式，歌词可能被改 | 不给就唱原歌词 |
| 片段长度 | 不超过 8 分钟 | 6 秒 ~ 6 分钟 |
| 片段怎么上传 | 服务商自带的临时文件区 | 匿名临时文件站 litterbox.catbox.moe（公开链接，默认 1 小时后删除） |

```bash
export SUNO_API_KEY=你的key          # 或 export REPLICATE_API_TOKEN=你的token
```

## 用法

先看有哪些风格：

```bash
python3 remix.py styles
```

内置 lofi、citypop、guofeng、edm、phonk、viral、rnb、rock、acoustic、jazz、piano、cinematic，可以在 `presets.toml` 里改或加。

**1. 先空跑一遍（不上传、不花钱）**：确认截取的位置对不对、请求体长什么样。

```bash
python3 remix.py cover 老歌.mp3 --style citypop --start 1:05 --duration 30 --lyrics 副歌.txt --dry-run
```

截好的片段在 `out/…/source.mp3`，可以先听一下。

**2. 给短视频配一段翻唱副歌**：不写 `--duration` 时按视频长度截取，成品和视频一样长，结尾自动淡出。

```bash
python3 remix.py cover 老歌.mp3 --style citypop --start 1:05 --lyrics 副歌.txt --video 视频.mp4
```

**3. 口播视频垫背景音乐**：只要纯伴奏，保留视频里的人声，配乐压低音量垫在下面。

```bash
python3 remix.py cover 老歌.mp3 --style lofi --instrumental --start 0:48 \
    --video 口播.mp4 --keep-video-audio --music-volume 0.3
```

**4. 不用预设，自己写风格**

```bash
python3 remix.py cover 老歌.mp3 --style "bossa nova, nylon guitar, soft percussion" --more "female vocals, slower"
```

**5. 换成 MiniMax**（不给歌词也会唱原歌词）

```bash
python3 remix.py cover 老歌.mp3 --provider minimax --style rnb --start 1:05 --duration 30
```

**6. 等太久超时了**：cover 会打印任务 ID（也记在 `meta.json` 里），稍后再取，不会重复扣费。

```bash
python3 remix.py fetch <任务ID> --provider suno --video 视频.mp4
```

**7. 换个视频，或者重新剪**：拿已经下载好的结果重新处理，不调用接口。

```bash
python3 remix.py finish out/…/raw_1.mp3 --video 另一个视频.mp4 --fade-out 3
```

所有参数见 `python3 remix.py cover --help`。

## 输出

```
out/20261028-153000-老歌-citypop/
  source.mp3    截取的片段（上传的就是它）
  raw_1.mp3     接口返回的原始结果
  final_1.mp3   处理好的配乐，可以直接拖进剪映
  final_1.mp4   合成好的视频（给了 --video 才有）
  raw_2.mp3 …   第二个版本
  meta.json     任务 ID、请求体、结果链接
```

## 小技巧

- **歌词文件只写截取的那几句**，可以带 `[verse]`、`[chorus]` 标签。歌词和片段对得上，旋律保留得最好。
- **`--keep` 调保留度**（只对 suno 有效）：0.7 以上很像原曲；0.5~0.65 换了风格、旋律还认得出；再低基本只保留情绪。预设里已经给了各风格的默认值。
- **一次出两版**，挑一版用。都不满意就换个 `--keep` 或 `--more` 再来一次。
- 成品默认标准化到 -14 LUFS（流媒体和短视频常用的响度），和其他素材放在一起不会忽大忽小。想更响一点就加 `--lufs -12`。

## 接口对不上时

第三方接口的字段时不时会变。本工具按服务商公开的文档编写，测试用的是模拟接口，**还没有拿真实密钥跑过**。第一次用建议先 `--dry-run`，再拿一段 20 秒左右的短片段试。

- 报字段错误：看 `--dry-run` 打印的请求体，用 `--set 键=值` 补字段或改字段，比如 `--set styleWeight=0.7`、`--set model=V4_5ALL`。
- 模型版本：`--model`，或在 `config.toml` 里设置 `[suno] model`。
- 换一家接口格式相同的服务商（比如 kie.ai）：在 `config.toml` 里改 `base` 和 `upload_base`。
- 上传失败：把 `out/…/source.mp3` 自己传到对象存储（阿里云 OSS、腾讯云 COS 等），拿到公网链接后加 `--audio-url 链接` 重新运行。
- 接口代码都在 `remixer/providers.py`，一个服务商一个类。

## 版权提醒

- 发到抖音、视频号就是公开传播。大多数老歌的词曲仍在保护期内（作者去世后 50 年），改编需要词曲作者授权，平台也可能识别后限流或下架。
- 不要用 AI 模仿某位歌手的声音、冒充原唱：《民法典》第 1023 条规定，自然人的声音参照肖像权保护。
- Suno 会识别并拦截上传的版权录音，原版唱片可能传不上去。用自己唱的、或者已经拿到授权的音频更稳。

## 测试

```bash
cd song-remix
python3 -m unittest discover -s tests
```

## 目录

```
remix.py              命令行入口（cover / fetch / finish / styles）
remixer/audio.py      ffmpeg：截取、裁剪淡出、响度标准化、合成视频
remixer/providers.py  云端接口：Suno（sunoapi.org）、MiniMax（Replicate）
presets.toml          风格预设
config.example.toml   配置模板（复制成 config.toml）
tests/                单元测试（模拟接口 + 真实 ffmpeg）
```
