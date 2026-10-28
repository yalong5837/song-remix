# 老歌改编：风格翻唱 + 短视频配乐

只知道歌名也能做：在网上找到这首歌的简谱或 MIDI，工具把它合成一段简单的旋律小样，交给云端模型（Suno / MiniMax）换个风格重新编曲、演唱，再自动裁成视频长度、加淡出、统一响度，可以直接合成进短视频。手里有现成的音频（自己唱的、已获授权的）也能直接用。

```
路线一（推荐）  歌名 ─网上找─▶ 简谱 / MIDI ─▶ 旋律小样 demo.wav ─┐
路线二          自己唱的 / 已获授权的音频 ─▶ 截取片段 ───────────┤
                                                                ▼
     云端翻唱（Suno / MiniMax）─▶ 下载 2 个版本 ─▶ 裁剪 · 淡出 · -14 LUFS ─▶ final_N.mp3 / final_N.mp4
```

## 准备

1. Python 3.11+，ffmpeg（要带 libmp3lame，常见发行版自带）
2. `git clone https://github.com/yalong5837/song-remix.git && cd song-remix`，再 `pip install -r requirements.txt`
3. 选一个接口，配好密钥（环境变量，或者把 `config.example.toml` 复制成 `config.toml` 再填）：

| | `suno`（默认） | `minimax` |
| --- | --- | --- |
| 来源 | [sunoapi.org](https://sunoapi.org) 转接的 Suno（Suno 官方没有开放 API） | [Replicate](https://replicate.com/minimax/music-cover) 上 MiniMax 的翻唱模型 |
| 密钥 | `SUNO_API_KEY` | `REPLICATE_API_TOKEN` |
| 一次出几版 | 通常 2 版 | 1 版 |
| 纯伴奏 `--instrumental` | 支持 | 不支持 |
| 保留度 `--keep` | 支持 | 不支持 |
| 片段长度 | 不超过 8 分钟 | 6 秒 ~ 6 分钟 |
| 片段怎么上传 | 服务商自带的临时文件区 | 匿名临时文件站 litterbox.catbox.moe（公开链接，默认 1 小时后删除） |

```bash
export SUNO_API_KEY=你的key          # 或 export REPLICATE_API_TOKEN=你的token
python3 remix.py check               # 检查 ffmpeg、依赖和密钥是否就绪
python3 remix.py styles              # 看有哪些风格预设
```

内置风格：lofi、citypop、guofeng、edm、phonk、viral、rnb、rock、acoustic、jazz、piano、cinematic，可以在 `presets.toml` 里改或加。

## 路线一：从歌名开始

1. **找谱**：搜“歌名 简谱”，最好找带歌词、带和弦的；也可以搜“歌名 MIDI”。短视频一般只用副歌，8~16 小节就够。
2. **写成简谱文本**：照下一节「简谱怎么写」敲成一个 txt，可以参考 [`examples/小星星.txt`](examples/小星星.txt)。对着图片敲，一段副歌几分钟就能敲完；也可以把谱子图片发给 Grok Bot、Muse、Claude 这类 AI，让它按这个格式转写。MIDI 文件不用转，直接用。
3. **检查**：工具会指出小节拍数不对、歌词字数和音数对不上的地方，对照原谱改到没有提示为止。

   ```bash
   python3 remix.py melody 副歌.txt --check
   ```

4. **听小样**：生成 `out/…/demo.wav`，听听旋律对不对。想变调、变速，加 `--transpose 2`（升两个半音）、`--tempo 100`。

   ```bash
   python3 remix.py melody 副歌.txt
   ```

5. **翻唱**：先空跑一遍（不上传、不花钱），没问题再去掉 `--dry-run` 正式提交。

   ```bash
   python3 remix.py cover 副歌.txt --style citypop --video 视频.mp4 --dry-run
   python3 remix.py cover 副歌.txt --style citypop --video 视频.mp4
   ```

   工具会先生成小样，歌词直接用谱子里的，保留度默认 0.75，让模型紧跟旋律。谱子比视频短时，会自动整段重复几遍。

**为什么不直接按歌名自动扒谱**：谱子网站大多是图片或 PDF，格式各不相同，也没有公开接口，自动抓取既不稳定，也可能违反网站条款。找谱这一步交给你自己，或者交给带浏览器的智能体（见下文「交给智能体用」），工具负责检查和生成。

## 简谱怎么写

```
// 两个斜杠后面是注释
标题: 小星星
调: 1=C          // 也可以写 1=D、1=bE、1=F#
拍号: 4/4        // 也可以写 3/4、6/8
速度: 90         // 每分钟拍数

[verse]          // 段落标签，会原样放进歌词
| 1 1 5 5 | 6 6 5 - |
词: 一闪一闪亮晶晶
和弦: C | F C |
```

| 写法 | 意思 |
| --- | --- |
| `1` ~ `7` | do re mi fa sol la si |
| `0` | 休止 |
| `5'`、`5''` | 高八度、高两个八度（简谱里音符上面的点） |
| `5,`、`5,,` | 低八度、低两个八度（音符下面的点） |
| `5_`、`5__` | 八分音符、十六分音符（音符下面一条、两条横线） |
| `5.` | 附点，时值乘 1.5 |
| `-` | 延长一拍（单独写，前后用空格隔开） |
| `#4`、`b7` | 升半音、降半音 |
| `\|` | 小节线（`\|\|`、`\|:`、`:\|` 也认，但不展开反复） |

- 音和音之间用空格隔开。标记可以叠加，比如 `6,_.` 是低音 la、附点八分。
- **歌词**：写在对应旋律行的下一行，以 `词:` 开头。中文一个字对一个音；一个字拖好几个音时，用 `_` 占位（比如 `爱_你`）；英文按音节用连字符分开（`twin-kle`）。
- **和弦**（可选）：以 `和弦:` 开头，用 `|` 分小节，和上面的旋律对齐；一个小节写两个和弦就各占一半。可以写 `C`、`Am`、`G7`、`Fmaj7`、`Bb`、`C#m`，也可以写级数 `1`、`4`、`5`、`6m`。没有和弦的小节写 `-`。
- 第一小节可以是弱起（不满一小节），最后一小节可以不满；其他小节拍数不对时，`--check` 会提示。
- 暂不支持三连音。

**MIDI**：`melody` 会列出所有声部，自动挑一个当旋律（优先挑名字像 melody / vocal / 主旋律 的声部，其次挑音高最高的）。挑错了就用 `--part 声部号` 指定。MIDI 里没有歌词，翻唱时要用 `--lyrics` 给歌词，或者加 `--instrumental`。

## 路线二：用现成的音频

音频要是你有权使用的：自己唱的、弹的，或者已经获得授权的。用 `--start`、`--duration` 截出要用的那段，`--lyrics` 给这段的歌词（一行一句，可以加 `[verse]`、`[chorus]`）。

```bash
python3 remix.py cover 我唱的.mp3 --style citypop --start 1:05 --duration 30 --lyrics 副歌.txt --dry-run
python3 remix.py cover 我唱的.mp3 --style citypop --start 1:05 --lyrics 副歌.txt --video 视频.mp4
```

截好的片段在 `out/…/source.mp3`，空跑时可以先听一下。用 `--provider minimax` 时不给歌词也行，它会唱原歌词。

## 常用场景

两条路线都适用：下面的 `谱.txt` 换成音频文件也一样。

**口播视频垫背景音乐**：只要纯伴奏，保留视频里的人声，配乐压低音量垫在下面。

```bash
python3 remix.py cover 谱.txt --style lofi --instrumental --video 口播.mp4 --keep-video-audio --music-volume 0.3
```

**不用预设，自己写风格**

```bash
python3 remix.py cover 谱.txt --style "bossa nova, nylon guitar, soft percussion" --more "female vocals, slower"
```

**提交完先去干别的，或者等太久超时了**：加 `--no-wait` 提交后马上返回，之后用输出目录取结果。结果放回原目录，视频和时长沿用提交时的设置；这一步只是查询任务，不会重复扣费。

```bash
python3 remix.py cover 谱.txt --style citypop --video 视频.mp4 --no-wait
python3 remix.py fetch out/20261028-153000-谱-citypop
```

只有任务 ID 也行：`python3 remix.py fetch <任务ID> --provider suno --video 视频.mp4`。

**换个视频，或者重新剪**：拿已经下载好的结果重新处理，不调用接口。

```bash
python3 remix.py finish out/…/raw_1.mp3 --video 另一个视频.mp4 --fade-out 3
```

所有参数见 `python3 remix.py cover --help`、`python3 remix.py melody --help`。

## 输出

```
out/20261028-153000-小星星-citypop/
  demo.wav      旋律小样（从曲谱出发时才有）
  source.mp3    截取的片段（上传的就是它）
  raw_1.mp3     接口返回的原始结果
  final_1.mp3   处理好的配乐，可以直接拖进剪映
  final_1.mp4   合成好的视频（给了 --video 才有）
  raw_2.mp3 …   第二个版本
  meta.json     任务 ID、请求体、结果链接、成品文件清单（files）
```

## 交给智能体用（Grok Bot、Meta Muse、Claude Code 等）

这个仓库本身就是一个 [Agent Skills](https://agentskills.io) 格式的技能：根目录的 `SKILL.md` 是写给 AI 看的说明，包括什么时候用、要先问用户哪些事、从歌名找谱的流程、参数怎么选、出错怎么办、哪些事不能做。技能目录名要保持 `song-remix`，和 `SKILL.md` 里的 `name` 一致。

- **Grok Bot、Meta Muse**：它们都自带一台云端电脑（有终端、文件系统和浏览器），可以自己上网找谱、转写，然后在那台电脑上跑这个工具。
  1. 打包：在仓库里运行 `git archive --format=zip --prefix=song-remix/ -o song-remix.zip HEAD`。
  2. 把 `song-remix.zip` 发给它（或者让它从 GitHub 克隆这个仓库；私有仓库要先给它接上 GitHub），告诉它：“解压后读 SKILL.md，按说明把《某首歌》的副歌改成 citypop，配我的视频。”
  3. 把 `SUNO_API_KEY` 或 `REPLICATE_API_TOKEN` 放进它的密钥存储，不要直接贴在对话里。
  4. 它跑通一次后，可以让它把用法存成自己的技能，下次直接用。
- **Claude Code**：克隆进技能目录，`git clone https://github.com/yalong5837/song-remix.git ~/.claude/skills/song-remix`。
- **Codex、Gemini CLI 等支持 Agent Skills 的工具**：同样克隆或复制进各自的技能目录。

Grok Bot 和 Muse 里“安装技能”的具体入口，我没找到官方说明，以上是通用做法。另一条路是做成远程 MCP 连接器（部署在你自己的服务器上），两家都支持，还没做。

## 小技巧

- **谱子只写要用的那一段**（比如副歌）。`--start`、`--duration` 只截音频，歌词不会跟着截。
- **`--keep` 调保留度**（只对 suno 有效）：
  - 从音频出发：0.7 以上很像原曲；0.5~0.65 换了风格、旋律还认得出；再低基本只保留情绪。预设里已经给了各风格的默认值。
  - 从曲谱出发：默认 0.75。模型把旋律唱跑了，就调到 0.85；成品听起来还像电子小样，就降到 0.65。
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

- 发到抖音、视频号就是公开传播。大多数老歌的词曲仍在保护期内（作者去世后 50 年），不论从谱子还是从音频出发，公开发布改编版都需要词曲作者授权，平台也可能识别后限流或下架。
- 扒谱只是把旋律写下来，旋律仍然属于作曲者；网上的谱子本身也可能有编配者的版权，自己用就好，不要再分发谱子文件。
- 不要用 AI 模仿某位歌手的声音、冒充原唱：《民法典》第 1023 条规定，自然人的声音参照肖像权保护。
- 服务商要求上传的内容是你有权使用的，Suno 还会识别并拦截上传的版权录音。

## 测试

```bash
python3 -m unittest discover -s tests
```

## 目录

```
SKILL.md              给智能体看的使用说明（Agent Skills 格式）
remix.py              命令行入口（melody / cover / fetch / finish / styles / check）
remixer/score.py      曲谱解析：简谱文本、MIDI，检查小节和歌词
remixer/synth.py      合成旋律小样（主旋律 + 和弦 + 节拍，纯 Python）
remixer/audio.py      ffmpeg：截取、裁剪淡出、响度标准化、合成视频
remixer/providers.py  云端接口：Suno（sunoapi.org）、MiniMax（Replicate）
examples/             简谱示例
presets.toml          风格预设
config.example.toml   配置模板（复制成 config.toml）
tests/                单元测试（模拟接口 + 真实 ffmpeg）
```
