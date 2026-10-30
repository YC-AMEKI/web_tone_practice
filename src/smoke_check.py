"""自检脚本: 不发声地验证核心逻辑(解析/判分/统计/合成/会话与错题), 窗口会一闪而过."""
import json
import sys
import tempfile
from pathlib import Path

import src.tone_practice as tp

# 音名解析
assert tp.parse_note_name("b") == (11, "B")
assert tp.parse_note_name("c") == (0, "C")
assert tp.parse_note_name("f#") == (6, "F#")
assert tp.parse_note_name("Bb") == (10, "Bb")
assert tp.parse_note_name("bB") == (10, "Bb")
assert tp.parse_note_name("b♭") == (10, "Bb")
assert tp.parse_note_name("bb") == (10, "Bb")
assert tp.parse_note_name("BB") == (10, "Bb")
assert tp.parse_note_name("♭B") == (10, "Bb")
assert tp.parse_note_name("♭") is None
assert tp.parse_note_name("升F") == (6, "F#")
assert tp.parse_note_name("降e") == (3, "Eb")
assert tp.parse_note_name("EB") == (3, "Eb")
assert tp.parse_note_name("X") is None
assert tp.parse_note_name("") is None
assert tp.note_midi("C", 4) == 60 and tp.note_midi("A", 4) == 69
for names in tp.SCALES.values():
    for name in names:
        assert tp.parse_note_name(name) is not None

tmp = Path(tempfile.mkdtemp())
app = tp.EarTrainer(mistakes_file=tmp / "mistakes.json")
app.audio.play = lambda midi: None  # 自检不发声
app.auto_next.set(False)
app.update_idletasks()

app.scale_box.set("G大调")
app.size_box.set("5")
app._on_settings_change()
assert set(app.note_buttons) == set(tp.SCALES["G大调"])  # 按钮随调性重建

app.start_session()
assert len(app.questions) == 5 and app.results == [None] * 5

app.questions[0] = ("F#", tp.note_midi("F#", 3))
app._enter_question(0)
app.answer("D")  # 答错 → 记错题并落盘
assert "不是" in app.feedback.cget("text") and "正确答案是" in app.feedback.cget("text")
assert (app.attempts, app.correct, app.streak) == (1, 0, 0)
assert app.mistakes[6][0] == "F#"
assert json.loads((tmp / "mistakes.json").read_text("utf-8")) == {
    "6": {"name": "F#", "count": 1, "tries": 1, "hits": 0}
}
# 兼容旧版只有 name/count 的错题文件
(tmp / "old.json").write_text(json.dumps({"0": {"name": "C", "count": 3}}), encoding="utf-8")
assert tp.load_mistakes(tmp / "old.json") == {0: ("C", 3, 0, 0)}

app.next_question()  # 第 1 题已答 → 进入第 2 题
app.questions[1] = ("A", 69)
app._enter_question(1)
app.answer("A")
assert app.correct == 1 and app.streak == 1

app.prev_question()  # 回看第 1 题(已答, 复盘)
assert "不是" in app.feedback.cget("text")
app.answer("A")  # 已作答的题不能再答
assert app.attempts == 2

app.next_question()  # 第 1 题已答 → 回到第 2 题
app.questions[2] = ("E", 64)
app._enter_question(2)
app.reveal_answer()  # 看答案计为错误
assert app.attempts == 3 and app.correct == 1 and app.streak == 0
assert app.mistakes[4][0] == "E"

app.next_question()  # 第 3 题已答 → 第 4 题
app.questions[3] = ("G", 55)
app._enter_question(3)
app.next_question()  # 未作答 → 跳过计为错误
assert "已跳过" in app.feedback.cget("text") and app.mistakes[7][0] == "G"
app.next_question()  # 前进到第 5 题

app.questions[4] = ("B", 71)
app._enter_question(4)
app.answer("B")
app.next_question()  # 最后一题已答 → 总结
assert not app.session_active
summary = app.feedback.cget("text")
assert "练习完成" in summary and "答对" in summary and "答错" in summary
assert (app.attempts, app.correct) == (5, 2)
assert set(app.mistakes) == {6, 4, 7}

app.start_session(from_mistakes=True)  # 错题练习: 只考错过的音, 选项仍是整个音阶
assert set(app.note_buttons) == set(tp.SCALES["G大调"])
assert all(name in {"F#", "E", "G"} for name, _midi in app.questions)
app.questions[0] = ("F#", 54)
app._enter_question(0)
app.answer("F#")  # 答对一次不再直接移出(正确率 1/2)
assert 6 in app.mistakes and app.mistakes[6][2:] == (2, 1)
for _ in range(18):  # 连续答对, 正确率达到 95% 才移出
    app.results[app.q_index] = None
    app.answer("F#")
assert 6 not in app.mistakes and len(app.mistakes) == 2
assert "移出错题集" in app.feedback.cget("text")
assert "6" not in json.loads((tmp / "mistakes.json").read_text("utf-8"))

# Web 版(纯静态页): 乐理字面量必须与桌面版保持一致
import re

page = (Path(__file__).resolve().parent.parent / "docs" / "index.html").read_text("utf-8")
assert json.loads(re.search(r"const SCALES = (\{.*?\});", page, re.DOTALL).group(1)) == tp.SCALES
assert json.loads(
    re.search(r"const OCTAVE_CHOICES = (\[.*?\]);", page, re.DOTALL).group(1)
) == tp.OCTAVE_CHOICES
assert json.loads(
    re.search(r"const SESSION_CHOICES = (\[.*?\]);", page, re.DOTALL).group(1)
) == tp.SESSION_CHOICES

import wave

with wave.open(str(app.audio.ensure_wav(60))) as w:
    assert w.getnframes() > tp.RATE  # 至少 1 秒的音频
app.destroy()
print("smoke: ALL OK")
sys.exit(0)
