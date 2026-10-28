"""曲谱 → 音符序列。支持两种输入：

- 简谱文本（.txt / .jianpu），写法见 README「简谱怎么写」；
- MIDI 文件（.mid / .midi），自动挑出旋律声部。

时间统一用“拍”做单位（四分音符 = 1 拍），音高用 MIDI 编号（中央 C = 60）。
解析时顺带检查常见的扒谱错误：小节拍数不对、歌词字数和音数对不上，问题记在 Score.warnings 里。
"""
import math
import re
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path

SCORE_EXTS = ('.txt', '.jianpu', '.mid', '.midi')
SCALE = (0, 2, 4, 5, 7, 9, 11)                       # 大调 1~7 的半音位置
LETTERS = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
CHORDS = {
    '': (0, 4, 7), 'maj': (0, 4, 7), 'm': (0, 3, 7), 'min': (0, 3, 7),
    '7': (0, 4, 7, 10), 'maj7': (0, 4, 7, 11), 'M7': (0, 4, 7, 11), 'm7': (0, 3, 7, 10),
    'dim': (0, 3, 6), 'aug': (0, 4, 8), '+': (0, 4, 8),
    'sus2': (0, 2, 7), 'sus4': (0, 5, 7), 'sus': (0, 5, 7),
    '6': (0, 4, 7, 9), 'm6': (0, 3, 7, 9), '9': (0, 4, 7, 14), 'add9': (0, 4, 7, 14),
}
LINE_KINDS = {
    '标题': 'title', 'title': 'title', '调': 'key', 'key': 'key', '拍号': 'time', 'time': 'time',
    '速度': 'tempo', 'tempo': 'tempo', 'bpm': 'tempo',
    '词': 'lyrics', 'lyrics': 'lyrics', '和弦': 'chords', 'chords': 'chords',
}
NOTE = re.compile(r"([#b]?)([0-7])([',_.]*)")
CJK = re.compile(r'[⺀-鿿豈-﫿가-힯]')
PUNCT = '，。、,.!?！？；;：:"“”‘’()（）《》「」…—~～'
MELODY_NAMES = re.compile(r'melody|vocal|voice|lead|sing|主旋律|旋律|人声|主唱', re.I)


@dataclass
class Note:
    start: float            # 从第几拍开始
    beats: float            # 持续几拍
    pitch: int | None       # MIDI 音高，None 表示休止


@dataclass
class Chord:
    start: float
    beats: float
    pitches: tuple


@dataclass
class Score:
    notes: list
    chords: list = field(default_factory=list)
    lyrics: list = field(default_factory=list)       # 一行一句，含 [chorus] 这类段落标签
    tempo: float = 90.0
    beats_per_bar: float = 4.0
    pickup: float = 0.0                              # 弱起小节的拍数，没有弱起是 0
    title: str = ''
    key: str = ''
    time: str = '4/4'
    bars: int = 0
    info: str = ''                                   # 给人看的补充说明（MIDI 用了哪个声部）
    warnings: list = field(default_factory=list)

    @property
    def beats(self):
        return max((n.start + n.beats for n in self.notes), default=0.0)

    @property
    def seconds(self):
        return self.beats * 60 / self.tempo

    @property
    def lyrics_text(self):
        return '\n'.join(self.lyrics)

    @property
    def has_lyrics(self):
        return any(not line.startswith('[') for line in self.lyrics)

    def repeat(self, times):
        """整首重复 times 遍（视频比谱子长时用），歌词跟着重复，每遍都从整小节开始。"""
        span = math.ceil(self.beats / self.beats_per_bar - 1e-9) * self.beats_per_bar

        def shifted(items):
            return [replace(x, start=x.start + k * span) for k in range(times) for x in items]
        return replace(self, notes=shifted(self.notes), chords=shifted(self.chords),
                       lyrics=self.lyrics * times, bars=self.bars * times)


def load(path, transpose=0, tempo=None, part=None):
    """按扩展名读取曲谱。transpose 是升降几个半音（变调），tempo 覆盖原速度（变速）。"""
    path = Path(path)
    if path.suffix.lower() in ('.mid', '.midi'):
        return read_midi(path, part, transpose, tempo)
    return parse_jianpu(path.read_text(encoding='utf-8'), transpose, tempo)


def is_score(path):
    return Path(path).suffix.lower() in SCORE_EXTS


# ---------- 简谱 ----------

def parse_key(text):
    """'1=C' / '1=bE' / 'F#' / 'Bb' → 相对 C 的半音数。"""
    m = re.fullmatch(r'(?:1\s*=\s*)?([#b]?)([A-Ga-g])([#b]?)', text.strip())
    if not m:
        raise ValueError(f'调号看不懂：{text}（写成 1=C、1=bE、1=F# 这样）')
    acc = m.group(1) + m.group(3)
    return (LETTERS[m.group(2).upper()] + acc.count('#') - acc.count('b')) % 12


def parse_time_sig(text):
    """'4/4' → 4 拍，'3/4' → 3 拍，'6/8' → 3 拍（都折算成四分音符）。"""
    m = re.fullmatch(r'(\d+)\s*/\s*(\d+)', text.strip())
    if not m:
        raise ValueError(f'拍号看不懂：{text}（写成 4/4、3/4、6/8 这样）')
    return int(m.group(1)) * 4 / int(m.group(2))


def parse_note(tok, key):
    """'#4'_ / "5'" / '6,.' 这样的音 → (MIDI 音高或 None, 拍数)。
    ' 高八度、, 低八度、_ 时值减半、. 附点，可以叠加。"""
    m = NOTE.fullmatch(tok)
    if not m:
        raise ValueError(tok)
    acc, digit, marks = m.groups()
    dots = marks.count('.')
    beats = 0.5 ** marks.count('_') * (1, 1.5, 1.75)[min(dots, 2)]
    if digit == '0':
        return None, beats
    shift = 12 * (marks.count("'") - marks.count(',')) + {'#': 1, 'b': -1}.get(acc, 0)
    return 60 + key + SCALE[int(digit) - 1] + shift, beats


def parse_chord(sym, key):
    """'Am7' / 'Bb' / 'C#m' / 级数写法 '6m'、'4' → 一组 MIDI 音高（根音在 C3~B3）。斜杠后的低音忽略。"""
    m = re.fullmatch(r'([#b]?)([A-G1-7])([#b]?)(.*?)(?:/.*)?', sym)
    if not m or m.group(4) not in CHORDS:
        raise ValueError(sym)
    acc, root = m.group(1) + m.group(3), m.group(2)
    base = LETTERS[root] if root.isalpha() else key + SCALE[int(root) - 1]
    root_pitch = 48 + (base + acc.count('#') - acc.count('b')) % 12
    return tuple(root_pitch + i for i in CHORDS[m.group(4)])


def syllables(text):
    """歌词 → 音节：中文一个字一个音节，英文按空格和连字符分；_ 表示拖腔（上一个字继续唱）。"""
    out = []
    for tok in text.split():
        if CJK.search(tok) or set(tok) == {'_'}:
            out += [c for c in tok if c not in PUNCT]
        else:
            out += [p for p in tok.strip(PUNCT).split('-') if p]
    return out


def lyric_line(text):
    """给模型的歌词：去掉拖腔符号和连字符，中文字之间不留空格。"""
    s = re.sub(r'\s+', ' ', text.replace('_', ' ')).strip()
    s = re.sub(r'(?<=\w)-(?=\w)', '', s)
    return re.sub(r'(?<=[⺀-￿]) (?=[⺀-￿])', '', s)


def _split_line(raw):
    """去掉 // 注释，认出“调: …”“词: …”这类行。返回 (类别, 内容)。"""
    line = raw.split('//')[0].strip()
    if not line:
        return None, ''
    if re.fullmatch(r'\[[^\]]+\]', line):
        return 'tag', line
    m = re.match(r'([^\s:：|]+)\s*[:：]\s*(.*)', line)
    if m and m.group(1).lower() in LINE_KINDS:
        return LINE_KINDS[m.group(1).lower()], m.group(2).strip()
    return 'notes', line


def parse_jianpu(text, transpose=0, tempo=None):
    lines = [(no, *_split_line(raw)) for no, raw in enumerate(text.splitlines(), 1)]
    meta = {'title': '', 'key': '1=C', 'time': '4/4', 'tempo': '90'}
    meta.update({kind: content for _, kind, content in lines if kind in meta})
    key = parse_key(meta['key'])
    bpb = parse_time_sig(meta['time'])
    found = re.search(r'\d+(?:\.\d+)?', meta['tempo'])
    if not found:
        raise ValueError(f'速度看不懂：{meta["tempo"]}（写成 速度: 90）')

    notes, lyrics, warnings, bars, pending_chords = [], [], [], [], []
    pos, bar_open = 0.0, False
    current = None                 # 最近一条旋律行：行号、唱的音数、是否已有歌词
    for no, kind, content in lines:
        if kind == 'tag':
            lyrics.append(content)
        elif kind == 'notes':
            current = {'no': no, 'sung': 0, 'lyrics': False}
            content = re.sub(r':?\|+:?', ' | ', unicodedata.normalize('NFKC', content))
            for tok in content.split():
                if tok == '|':
                    if bar_open:
                        bars[-1]['end'], bar_open = pos, False
                    continue
                if not bar_open:
                    bars.append({'start': pos, 'line': no})
                    bar_open = True
                if tok == '-':
                    if not notes:
                        raise ValueError(f'第 {no} 行：谱子不能以 - 开头')
                    notes[-1].beats += 1
                    pos += 1
                    continue
                try:
                    pitch, beats = parse_note(tok, key)
                except ValueError:
                    raise ValueError(f'第 {no} 行：看不懂“{tok}”（音写成 1~7，0 是休止，见 README「简谱怎么写」）') from None
                notes.append(Note(pos, beats, pitch))
                current['sung'] += pitch is not None
                pos += beats
        elif kind == 'lyrics':
            if current is None:
                warnings.append(f'第 {no} 行：歌词前面没有旋律行，已忽略')
            elif current['lyrics']:
                warnings.append(f'第 {no} 行：同一行旋律有多行歌词，只用第一行')
            else:
                current['lyrics'] = True
                n = len(syllables(content))
                if n != current['sung']:
                    warnings.append(f'第 {no} 行：歌词 {n} 个字，上面的旋律有 {current["sung"]} 个音'
                                    '（一个字拖好几个音时，用 _ 占位）')
                lyrics.append(lyric_line(content))
        elif kind == 'chords':
            if current is None:
                warnings.append(f'第 {no} 行：和弦前面没有旋律行，已忽略')
                continue
            groups = [g.split() for g in content.split('|')]
            while groups and not groups[0]:
                groups.pop(0)
            while groups and not groups[-1]:
                groups.pop()
            line_bars = [i for i, b in enumerate(bars) if b['line'] == current['no']]
            if len(groups) > len(line_bars):
                warnings.append(f'第 {no} 行：写了 {len(groups)} 组和弦，上面的旋律只有 {len(line_bars)} 个小节')
            pending_chords += [(bi, syms, no) for bi, syms in zip(line_bars, groups)]
    if not notes:
        raise ValueError('谱子里没有找到音符')
    if bar_open:
        bars[-1]['end'] = pos

    for i, bar in enumerate(bars):
        length = bar['end'] - bar['start']
        if abs(length - bpb) > 1e-6 and 0 < i < len(bars) - 1:   # 第一小节可以弱起，最后一小节可以不满
            warnings.append(f'第 {bar["line"]} 行：第 {i + 1} 小节是 {length:g} 拍，拍号 {meta["time"]} 要求 {bpb:g} 拍')

    chords = []
    for bi, syms, no in pending_chords:
        bar = bars[bi]
        each = (bar['end'] - bar['start']) / len(syms)
        for k, sym in enumerate(syms):
            if sym == '-':
                continue
            try:
                chords.append(Chord(bar['start'] + k * each, each, parse_chord(sym, key)))
            except ValueError:
                raise ValueError(f'第 {no} 行：和弦看不懂“{sym}”（写成 C、Am、G7、Fmaj7，或级数 1、6m）') from None

    first = bars[0]['end'] - bars[0]['start']
    for n in notes:
        if n.pitch is not None:
            n.pitch += transpose
    for c in chords:
        c.pitches = tuple(p + transpose for p in c.pitches)
    return Score(notes=notes, chords=chords, lyrics=lyrics, tempo=float(tempo or found.group()),
                 beats_per_bar=bpb, pickup=first if first < bpb - 1e-6 and len(bars) > 1 else 0.0,
                 title=meta['title'], key=meta['key'], time=meta['time'], bars=len(bars), warnings=warnings)


# ---------- MIDI ----------

def _midi_events(data):
    """解析标准 MIDI 文件。返回 (每拍 tick 数, 声部列表, 速度列表, 每小节拍数)。
    声部按（音轨, 通道）分开，跳过鼓（通道 10）。"""
    if data[:4] != b'MThd':
        raise ValueError('不是 MIDI 文件')
    division = int.from_bytes(data[12:14], 'big')
    if division & 0x8000:
        raise ValueError('不支持 SMPTE 时间格式的 MIDI')
    parts, names, tempos, bpb = {}, {}, [], None
    pos, track = 8 + int.from_bytes(data[4:8], 'big'), 0
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], int.from_bytes(data[pos + 4:pos + 8], 'big')
        chunk, pos = data[pos + 8:pos + 8 + size], pos + 8 + size
        if cid != b'MTrk':
            continue
        i, tick, status, held = 0, 0, None, {}

        def vlq():
            nonlocal i
            value = 0
            while True:
                b = chunk[i]
                i += 1
                value = (value << 7) | (b & 0x7F)
                if b < 0x80:
                    return value
        while i < len(chunk):
            tick += vlq()
            b = chunk[i]
            if b == 0xFF:                                    # 元事件
                kind = chunk[i + 1]
                i += 2
                n = vlq()
                payload, i = chunk[i:i + n], i + n
                if kind == 0x51:
                    tempos.append((tick, int.from_bytes(payload, 'big')))
                elif kind == 0x58 and bpb is None:
                    bpb = payload[0] * 4 / 2 ** payload[1]
                elif kind == 0x03:
                    for enc in ('utf-8', 'gbk', 'latin-1'):
                        try:
                            names[track] = payload.decode(enc).strip()
                            break
                        except UnicodeDecodeError:
                            pass
                continue
            if b in (0xF0, 0xF7):                            # 系统独占消息
                i += 1
                i += vlq()
                continue
            if b & 0x80:
                status, i = b, i + 1
            elif status is None:
                raise ValueError('MIDI 数据损坏')
            kind, channel = status & 0xF0, status & 0x0F
            if kind in (0xC0, 0xD0):
                i += 1
                continue
            note, velocity = chunk[i], chunk[i + 1]
            i += 2
            if kind == 0x90 and velocity:
                held.setdefault((channel, note), []).append(tick)
            elif kind in (0x80, 0x90) and held.get((channel, note)):
                start = held[(channel, note)].pop(0)
                if channel != 9:
                    parts.setdefault((track, channel), []).append((start, tick, note))
        track += 1
    found = [{'track': t, 'channel': c, 'name': names.get(t, ''), 'notes': sorted(ns)}
             for (t, c), ns in sorted(parts.items()) if ns]
    for k, p in enumerate(found, 1):
        p['id'] = k
        p['mean'] = sum(n[2] for n in p['notes']) / len(p['notes'])
    return division, found, tempos, bpb or 4.0


def midi_parts(path):
    return _midi_events(Path(path).read_bytes())[1]


def pick_part(parts):
    """猜哪个声部是主旋律：先看音轨名，再在音符数够多的声部里挑平均音高最高的。"""
    named = [p for p in parts if MELODY_NAMES.search(p['name'])]
    if named:
        return named[0]
    most = max(len(p['notes']) for p in parts)
    return max((p for p in parts if len(p['notes']) >= most * 0.1), key=lambda p: p['mean'])


def read_midi(path, part=None, transpose=0, tempo=None):
    division, parts, tempos, bpb = _midi_events(Path(path).read_bytes())
    if not parts:
        raise ValueError('MIDI 里没有找到旋律音符')
    if part is not None and not 1 <= part <= len(parts):
        raise ValueError(f'没有第 {part} 声部，这个 MIDI 有 {len(parts)} 个声部')
    chosen = parts[part - 1] if part else pick_part(parts)

    mono = []                        # 单音化：同时响的音只留最高的
    for start, end, pitch in sorted(chosen['notes'], key=lambda n: (n[0], -n[2])):
        if mono and start < mono[-1][1]:
            if start == mono[-1][0]:
                continue
            mono[-1][1] = start
        mono.append([start, end, pitch])
    shift = math.floor(mono[0][0] / division / bpb) * bpb      # 去掉开头的空小节
    notes = [Note(s / division - shift, (e - s) / division, p + transpose) for s, e, p in mono]

    warnings = []
    if len({t for _, t in tempos}) > 1:
        warnings.append('这个 MIDI 中途变速，小样统一用开头的速度')
    listing = '；'.join(f"{p['id']}{'（' + p['name'] + '）' if p['name'] else ''} {len(p['notes'])} 个音"
                        + (' ← 用这个' if p is chosen else '') for p in parts)
    beats = max(n.start + n.beats for n in notes)
    return Score(notes=notes, tempo=float(tempo or (60e6 / tempos[0][1] if tempos else 120)),
                 beats_per_bar=bpb, title=Path(path).stem, key='MIDI',
                 time=f'{bpb:g}/4', bars=math.ceil(beats / bpb), info=f'声部：{listing}', warnings=warnings)
