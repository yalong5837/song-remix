import tempfile
import unittest
from pathlib import Path

from fakes import make_midi

from remixer import score

EXAMPLE = Path(__file__).resolve().parents[1] / 'examples' / '小星星.txt'


def jp(body, **kw):
    return score.parse_jianpu(body, **kw)


class JianpuTest(unittest.TestCase):
    def test_note_tokens(self):
        s = jp("| 1 2_ 3__ 4. 5_ - 0 1' 1, #4 b7 6'_. |")
        self.assertEqual([n.pitch for n in s.notes], [60, 62, 64, 65, 67, None, 72, 48, 66, 70, 81])
        self.assertEqual([n.beats for n in s.notes], [1, 0.5, 0.25, 1.5, 1.5, 1, 1, 1, 1, 1, 0.75])
        self.assertEqual([n.start for n in s.notes[:4]], [0, 1, 1.5, 1.75])
        self.assertEqual(s.warnings, [])

    def test_headers_key_and_transpose(self):
        self.assertEqual(jp('调: 1=D\n1').notes[0].pitch, 62)
        self.assertEqual(jp('调：1=bE\n1').notes[0].pitch, 63)
        self.assertEqual(jp('key: F#\n1').notes[0].pitch, 66)
        self.assertEqual(jp('调: 1=Bb\n1').notes[0].pitch, 70)
        self.assertEqual(jp('调: 1=D\n1', transpose=-12).notes[0].pitch, 50)
        s = jp('标题: 测试\n速度: ♩=72\n1', tempo=None)
        self.assertEqual((s.title, s.tempo), ('测试', 72))
        self.assertEqual(jp('速度: 72\n1', tempo=100).tempo, 100)
        with self.assertRaisesRegex(ValueError, '调号'):
            jp('调: 1=H\n1')

    def test_bar_check_allows_pickup_and_last_bar(self):
        s = jp('拍号: 3/4\n| 5 | 1 2 3 | 4 5 | 6 7 1\' | 1 - |')
        self.assertEqual(s.beats_per_bar, 3)
        self.assertEqual(s.pickup, 1)
        self.assertEqual(len(s.warnings), 1)
        self.assertIn('第 3 小节是 2 拍', s.warnings[0])
        self.assertEqual(jp('拍号: 6/8\n1').beats_per_bar, 3)

    def test_bars_continue_across_lines_and_ties(self):
        s = jp('| 1 2 3\n4 | - 5 6 7 |\n| 1 - - - |')
        self.assertEqual(s.warnings, [])
        self.assertEqual(s.bars, 3)
        self.assertEqual(s.notes[3].beats, 2)        # 4 跨小节线延长一拍

    def test_lyrics(self):
        s = jp('[chorus]\n| 1 2 3 - | 5 0 5 5 |\n词: 你问我 爱_你\n| 1 2 3 4 |\n词: twin-kle twin-kle')
        self.assertEqual(s.lyrics, ['[chorus]', '你问我爱你', 'twinkle twinkle'])
        self.assertTrue(s.has_lyrics)
        self.assertEqual(s.warnings, [])
        bad = jp('| 1 2 3 4 |\n词: 一闪一闪亮')
        self.assertIn('歌词 5 个字，上面的旋律有 4 个音', bad.warnings[0])
        self.assertFalse(jp('[verse]\n1 2').has_lyrics)

    def test_syllables(self):
        self.assertEqual(score.syllables('一闪，一闪 亮_晶晶！'), ['一', '闪', '一', '闪', '亮', '_', '晶', '晶'])
        self.assertEqual(score.syllables('Hap-py birth-day, to you'), ['Hap', 'py', 'birth', 'day', 'to', 'you'])

    def test_chords(self):
        s = jp('调: 1=D\n| 1 2 3 4 | 5 6 7 1\' | 1 - - - |\n和弦: D | Bm7 G/B | - |')
        self.assertEqual([(c.start, c.beats, c.pitches) for c in s.chords],
                         [(0, 4, (50, 54, 57)), (4, 2, (59, 62, 66, 69)), (6, 2, (55, 59, 62))])
        deg = jp('调: 1=D\n| 1 2 3 4 | 5 6 7 1\' |\n和弦: 1 | 6m 4 |')
        self.assertEqual([c.pitches for c in deg.chords], [(50, 54, 57), (59, 62, 66), (55, 59, 62)])
        many = jp('| 1 2 3 4 |\n和弦: C | G |')
        self.assertIn('2 组和弦', many.warnings[0])
        with self.assertRaisesRegex(ValueError, '第 2 行：和弦看不懂“Hm”'):
            jp('| 1 2 3 4 |\n和弦: Hm |')

    def test_bad_token_reports_line(self):
        with self.assertRaisesRegex(ValueError, '第 3 行：看不懂“8”'):
            jp('调: 1=C\n| 1 2 3 4 |\n| 5 6 7 8 |')
        with self.assertRaisesRegex(ValueError, '没有找到音符'):
            jp('标题: 空\n词: 啊')
        with self.assertRaisesRegex(ValueError, '不能以 - 开头'):
            jp('- 1')

    def test_fullwidth_and_comments(self):
        s = jp('// 注释\n｜１　２　３　４ ｜ // 行尾注释\n|: 5 6 7 1\' :|')
        self.assertEqual([n.pitch for n in s.notes], [60, 62, 64, 65, 67, 69, 71, 72])
        self.assertEqual(s.warnings, [])

    def test_repeat_keeps_bars_aligned(self):
        s = jp('| 5 | 1 2 3 4 | 5 - - |\n词: 啊 一二三四五')
        r = s.repeat(3)
        self.assertEqual(len(r.notes), 3 * len(s.notes))
        self.assertEqual(r.notes[len(s.notes)].start, 8)      # 1+4+3=8 拍，正好两小节
        self.assertEqual(r.lyrics, s.lyrics * 3)
        self.assertAlmostEqual(r.seconds, s.seconds + 2 * 8 * 60 / s.tempo)

    def test_example_file(self):
        s = score.load(EXAMPLE)
        self.assertEqual((s.title, s.bars, s.warnings), ('小星星', 12, []))
        self.assertEqual(len(s.lyrics), 7)
        self.assertEqual(s.seconds, 32)


class MidiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def midi(self, parts, **kw):
        path = self.dir / 'song.mid'
        make_midi(path, parts, **kw)
        return path

    def test_picks_highest_part_and_skips_drums(self):
        bass = ('Bass', 1, [(b, 1, 40) for b in range(16)])
        melody = ('', 0, [(8, 1, 72), (9, 0.5, 74), (9.5, 0.5, 76), (10, 2, 77)])
        drums = ('Drums', 9, [(b, 0.5, 36) for b in range(16)])
        s = score.read_midi(self.midi([bass, melody, drums]))
        self.assertEqual([(n.start, n.beats, n.pitch) for n in s.notes],
                         [(0, 1, 72), (1, 0.5, 74), (1.5, 0.5, 76), (2, 2, 77)])   # 开头两个空小节去掉
        self.assertEqual(s.tempo, 100)
        self.assertEqual(s.title, 'song')
        self.assertIn('← 用这个', s.info)
        self.assertNotIn('Drums', s.info)

    def test_named_part_wins_and_part_option(self):
        path = self.midi([('Piano', 0, [(0, 1, 84), (1, 1, 86)]), ('Vocal', 1, [(0, 2, 64), (2, 2, 65)])])
        self.assertEqual(score.read_midi(path).notes[0].pitch, 64)
        self.assertEqual(score.read_midi(path, part=1).notes[0].pitch, 84)
        self.assertEqual(score.read_midi(path, part=2, transpose=2).notes[0].pitch, 66)
        with self.assertRaisesRegex(ValueError, '没有第 3 声部'):
            score.read_midi(path, part=3)
        self.assertEqual(len(score.midi_parts(path)), 2)

    def test_polyphony_is_reduced_to_top_line(self):
        chordy = ('Lead', 0, [(0, 2, 60), (0, 2, 64), (0, 2, 67), (1, 2, 72)])
        s = score.read_midi(self.midi([chordy]), tempo=80)
        self.assertEqual([(n.start, n.beats, n.pitch) for n in s.notes], [(0, 1, 67), (1, 2, 72)])
        self.assertEqual(s.tempo, 80)

    def test_three_four_time(self):
        s = score.read_midi(self.midi([('Lead', 0, [(3, 1, 60)])], numerator=3))
        self.assertEqual((s.beats_per_bar, s.notes[0].start), (3, 0))

    def test_not_midi(self):
        bad = self.dir / 'bad.mid'
        bad.write_bytes(b'RIFF....')
        with self.assertRaisesRegex(ValueError, '不是 MIDI'):
            score.load(bad)


if __name__ == '__main__':
    unittest.main()
