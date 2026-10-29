"""音准练习 —— 听音辨名 / 绝对音感训练小工具.

用法:
    python3 tone_practice.py

依赖: 仅 Python 标准库; 若已安装 numpy 则音色合成更快(可选).
平台: macOS (afplay) / Windows (winsound) / Linux (aplay 或 paplay).

玩法: 选好「调性 + 八度 + 题数」→ 点「开始练习」→ 听音点击音名作答, 答对答错都自动进入下一题;
      一组结束统计本轮答对/答错哪些音.
错题: 答错、跳过或看答案的音自动进错题集(答对一次即移出, 保存在用户主目录), 可随时专项练习.
快捷键: 空格 = 下一题(没有进行中的练习时开始一组).
"""

from __future__ import annotations

import json
import math
import os
import queue
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import wave
from array import array
from pathlib import Path
from tkinter import ttk

try:
    import numpy as _np
except ImportError:  # numpy 只是加速项, 缺失时回退到纯 Python 合成
    _np = None

# ============ 乐理: 音名 / 音阶 ============

LETTER_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
LETTER_SOLFEGE = {
    "C": "do",
    "D": "re",
    "E": "mi",
    "F": "fa",
    "G": "sol",
    "A": "la",
    "B": "si",
}

# 每个调性在「以 C 为界的一个八度」内的音名(决定答案的规范写法)
SCALES: dict[str, list[str]] = {
    "C大调": ["C", "D", "E", "F", "G", "A", "B"],
    "G大调": ["G", "A", "B", "C", "D", "E", "F#"],
    "D大调": ["D", "E", "F#", "G", "A", "B", "C#"],
    "A大调": ["A", "B", "C#", "D", "E", "F#", "G#"],
    "E大调": ["E", "F#", "G#", "A", "B", "C#", "D#"],
    "B大调": ["B", "C#", "D#", "E", "F#", "G#", "A#"],
    "F大调": ["F", "G", "A", "Bb", "C", "D", "E"],
    "降B大调": ["Bb", "C", "D", "Eb", "F", "G", "A"],
    "降E大调": ["Eb", "F", "G", "Ab", "Bb", "C", "D"],
    "降A大调": ["Ab", "Bb", "C", "Db", "Eb", "F", "G"],
    "降D大调": ["Db", "Eb", "F", "Gb", "Ab", "Bb", "C"],
    "a小调(和声)": ["A", "B", "C", "D", "E", "F", "G#"],
    "e小调(和声)": ["E", "F#", "G", "A", "B", "C", "D#"],
    "b小调(和声)": ["B", "C#", "D", "E", "F#", "G", "A#"],
    "d小调(和声)": ["D", "E", "F", "G", "A", "Bb", "C#"],
    "g小调(和声)": ["G", "A", "Bb", "C", "D", "Eb", "F#"],
    "c小调(和声)": ["C", "D", "Eb", "F", "G", "Ab", "B"],
    "半音阶": ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"],
}

OCTAVE_RANGE = range(1, 7)  # 可练的八度范围
OCTAVE_CHOICES = ["随机", *map(str, OCTAVE_RANGE)]
SESSION_CHOICES = ["5", "10", "20", "50"]  # 一组练习的题数
MISTAKES_FILE = Path.home() / ".tone_practice_mistakes.json"  # 错题集存放处


def parse_note_name(text: str) -> tuple[int, str] | None:
    """解析音名输入 → (半音数, 规范写法); 无法解析返回 None.

    支持的写法: C / c / F# / f♯ / Bb / bB / ♭B / b♭ / 升F / 降E …
    (# / ♯ / 升 记升号, b / ♭ / 降 记降号, 大小写不限)
    """
    s = text.strip().replace(" ", "")
    if not s:
        return None
    if s in ("b", "B"):  # 单独的 b/B 就是音名 B
        return LETTER_PC["B"], "B"
    letters: list[str] = []
    accidentals = 0  # 每个升号 +1, 每个降号 -1
    latin_b = 0  # 拉丁字母 b 的个数: 可作音名 B, 也可作降号
    for ch in s:
        if ch in ("#", "♯", "＃", "升"):
            accidentals += 1
        elif ch in ("♭", "ｂ", "降"):
            accidentals -= 1
        elif ch == "b":
            latin_b += 1
        elif ch.upper() in LETTER_PC:
            letters.append(ch.upper())
        else:
            return None
    # 拉丁 b 的角色: 已有其他字母时全部当降号(如 "Bb"/"bB" = B♭);
    # 没有字母时第一个当音名 B(如 "b♯"/"b♭"/"bb")
    if latin_b:
        if letters:
            accidentals -= latin_b
        else:
            letters.append("B")
            accidentals -= latin_b - 1
    # 兼容全大写的降号, 如把 E♭ 敲成 "EB"
    if len(letters) == 2 and letters[1] == "B":
        letters.pop()
        accidentals -= 1
    if len(letters) != 1:
        return None
    pc = (LETTER_PC[letters[0]] + accidentals) % 12
    sign = "#" if accidentals > 0 else "b"
    return pc, letters[0] + sign * abs(accidentals)


def note_midi(name: str, octave: int) -> int:
    """「音名 + 八度」→ MIDI 编号, 如 ("F#", 3) → 54 (C4=60, A4=69)."""
    parsed = parse_note_name(name)
    if parsed is None:
        raise ValueError(f"无法识别的音名: {name!r}")
    return (octave + 1) * 12 + parsed[0]


def midi_to_freq(midi: int) -> float:
    return 440.0 * 2.0 ** ((midi - 69) / 12.0)


def display_name(name: str) -> str:
    """把 "F#" / "Bb" 显示成 "F♯" / "B♭"."""
    return name.replace("#", "♯").replace("b", "♭")


def solfege(name: str) -> str:
    """音名 → 唱名, 如 "F#" → "fa♯"."""
    return LETTER_SOLFEGE[name[0]] + display_name(name[1:])


# ============ 内置钢琴音源(纯合成, 不需要任何音频文件) ============

RATE = 44100 if _np is not None else 22050
PARTIALS = 8 if _np is not None else 6  # 泛音个数; 纯 Python 路径取少些保证速度
INHARMONICITY = 0.0002  # 弦的不谐和性: 泛音比整数倍略高, 更接近真钢琴
ATTACK_SEC = 0.003  # 起音时长
FADE_SEC = 0.08  # 尾部淡出, 避免爆音
PEAK = 0.75  # 归一化峰值幅度


def _note_shape(freq: float) -> tuple[float, float]:
    """音越高越短促 → 返回 (采样时长, 基音衰减时间常数), 单位秒."""
    duration = min(3.2, max(1.4, 2.8 * (261.626 / freq) ** 0.55))
    tau = min(1.8, max(0.4, 1.1 * (220.0 / freq) ** 0.45))
    return duration, tau


def _partial_params(
    k: int, freq: float, tau: float
) -> tuple[float, float, float] | None:
    """第 k 个泛音的 (频率, 幅度, 衰减常数); 频率过高则返回 None."""
    f_k = k * freq * math.sqrt(1.0 + INHARMONICITY * k * k)
    if f_k >= RATE * 0.45:
        return None
    amp = k**-1.3 * math.exp(-0.15 * (k - 1))  # 音槌激发的频谱滚降
    tau_k = tau / (0.7 + 0.3 * k)  # 高泛音衰减更快
    return f_k, amp, tau_k


def _synth_numpy(freq: float, duration: float, tau: float) -> bytes:
    n = int(duration * RATE)
    t = _np.arange(n) / RATE
    sig = _np.zeros(n)
    for k in range(1, PARTIALS + 1):
        p = _partial_params(k, freq, tau)
        if p is None:
            break
        f_k, amp, tau_k = p
        sig += amp * _np.exp(-t / tau_k) * _np.sin(2.0 * math.pi * f_k * t)
    sig *= 1.0 - _np.exp(-t / ATTACK_SEC)  # 起音包络
    fade = int(FADE_SEC * RATE)
    sig[n - fade :] *= _np.linspace(1.0, 0.0, fade)
    sig *= PEAK / max(1e-9, float(_np.abs(sig).max()))
    return (sig * 32767.0).astype("<i2").tobytes()


def _synth_pure(freq: float, duration: float, tau: float) -> bytes:
    n = int(duration * RATE)
    buf = [0.0] * n
    two_pi = 2.0 * math.pi
    for k in range(1, PARTIALS + 1):
        p = _partial_params(k, freq, tau)
        if p is None:
            break
        f_k, amp, tau_k = p
        omega = two_pi * f_k / RATE
        decay = math.exp(-1.0 / (RATE * tau_k))
        env, phase = amp, 0.0
        for i in range(n):
            phase += omega
            buf[i] += env * math.sin(phase)
            env *= decay
    # 一次遍历套上起音/淡出包络并归一化
    a = 1.0 / (ATTACK_SEC * RATE)
    fade_from = n - int(FADE_SEC * RATE)
    fade_len = n - fade_from
    peak = 1e-12
    for i in range(n):
        g = 1.0 - math.exp(-a * i)
        if i >= fade_from:
            g *= (n - i) / fade_len
        v = buf[i] * g
        buf[i] = v
        peak = max(peak, abs(v))
    scale = PEAK / peak * 32767.0
    return array("h", (int(v * scale) for v in buf)).tobytes()


def synthesize(midi: int) -> bytes:
    """合成一个音高的钢琴音色, 返回 16bit 单声道 PCM."""
    freq = midi_to_freq(midi)
    duration, tau = _note_shape(freq)
    if _np is not None:
        return _synth_numpy(freq, duration, tau)
    return _synth_pure(freq, duration, tau)


class PianoVoice:
    """内置钢琴: 负责「合成 → WAV 缓存 → 调用系统播放器播放」."""

    def __init__(self, cache_dir: Path | str | None = None) -> None:
        if cache_dir is None:
            cache_dir = Path(tempfile.gettempdir()) / "tone_practice_piano"
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def path_for(self, midi: int) -> Path:
        return self.cache_dir / f"piano_{midi}_{RATE}.wav"

    def ensure_wav(self, midi: int) -> Path:
        """确保该音的 WAV 已生成(有缓存则直接返回)."""
        path = self.path_for(midi)
        if not path.exists():
            pcm = synthesize(midi)
            fd, tmp = tempfile.mkstemp(dir=self.cache_dir, suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as f, wave.open(f, "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(RATE)
                    w.writeframes(pcm)
                os.replace(tmp, path)  # 原子替换, 避免读到半成品
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        return path

    def play(self, midi: int) -> None:
        """播放一个音(会打断当前正在播的)."""
        path = self.ensure_wav(midi)
        with self._lock:
            self._stop_locked()
            self._spawn(str(path))

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    # ---- 内部实现 ----

    def _stop_locked(self) -> None:
        if sys.platform == "win32":
            import winsound

            winsound.PlaySound(None, winsound.SND_PURGE)
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None

    def _spawn(self, path: str) -> None:
        if sys.platform == "darwin":
            self._proc = subprocess.Popen(["afplay", path])
            return
        if sys.platform == "win32":
            import winsound

            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            return
        for player in (("paplay",), ("aplay", "-q")):
            if shutil.which(player[0]):
                self._proc = subprocess.Popen([*player, path])
                return
        raise RuntimeError("未找到可用的系统播放器 (afplay / aplay / paplay)")


def preload(voice: PianoVoice, midis: list[int], progress: queue.Queue) -> None:
    """后台预热音源缓存; 进度发到 progress 队列, 结束发 None."""
    total = len(midis)
    for done, midi in enumerate(midis, start=1):
        voice.ensure_wav(midi)
        progress.put((done, total))
    progress.put(None)


# ============ 错题集存取 ============


def load_mistakes(path: Path) -> dict[int, tuple[str, int]]:
    """读取错题集 {音高: (音名, 错误次数)}; 文件缺失或损坏时返回空集."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    mistakes: dict[int, tuple[str, int]] = {}
    for pc, item in data.items():
        try:
            mistakes[int(pc)] = (str(item["name"]), int(item["count"]))
        except (KeyError, TypeError, ValueError):
            continue
    return mistakes


def save_mistakes(path: Path, mistakes: dict[int, tuple[str, int]]) -> None:
    payload = {
        str(pc): {"name": name, "count": count} for pc, (name, count) in mistakes.items()
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


# ============ 界面 ============

GREEN = "#15803d"
RED = "#b91c1c"
GRAY = "#6b7280"
DARK = "#374151"
NOTE_BG = "#f3f4f6"  # 音名按钮默认底色
NOTE_BG_ACTIVE = "#e5e7eb"


class EarTrainer(tk.Tk):
    """主窗口: 开始一组练习 → 听音作答 → 翻题/复盘 → 总结与错题."""

    def __init__(self, mistakes_file: Path | str | None = None) -> None:
        super().__init__()
        self.title("音准练习 · 听音辨名")
        self.resizable(False, False)

        self.audio = PianoVoice()
        self.mistakes_file = Path(mistakes_file) if mistakes_file else MISTAKES_FILE
        self.mistakes = load_mistakes(self.mistakes_file)
        self._progress_q: queue.Queue = queue.Queue()
        midis = [(octave + 1) * 12 + pc for octave in OCTAVE_RANGE for pc in range(12)]
        threading.Thread(
            target=preload, args=(self.audio, midis, self._progress_q), daemon=True
        ).start()

        # 练习会话: 一组题固定为 N 道, 上一题/下一题在其间翻动
        self.questions: list[tuple[str, int]] = []
        self.results: list[tuple[bool, str | None, str] | None] = []  # (对错, 点的音, 反馈)
        self.q_index = 0
        self.session_active = False
        self.mistake_session = False
        self.expected_name: str | None = None
        self.expected_midi: int | None = None
        self._auto_after: str | None = None
        # 统计
        self.attempts = 0
        self.correct = 0
        self.streak = 0
        self.best_streak = 0
        self.history: list[tuple[str, bool]] = []

        self._build_ui()
        self._poll_preload()

    # ---- 界面搭建 ----

    def _build_ui(self) -> None:
        pad = {"padx": 18, "pady": 5}

        header = ttk.Frame(self)
        header.pack(fill="x", **pad)
        ttk.Label(header, text="🎹 音准练习", font=("", 22, "bold")).pack()
        ttk.Label(header, text="听音辨名 · 绝对音感训练", foreground=GRAY).pack()

        settings = ttk.Frame(self)
        settings.pack(fill="x", **pad)
        ttk.Label(settings, text="调性").pack(side="left")
        self.scale_box = ttk.Combobox(
            settings, values=list(SCALES), state="readonly", width=13
        )
        self.scale_box.set("C大调")
        self.scale_box.pack(side="left", padx=(4, 14))
        ttk.Label(settings, text="八度").pack(side="left")
        self.octave_box = ttk.Combobox(
            settings, values=OCTAVE_CHOICES, state="readonly", width=5
        )
        self.octave_box.set("4")
        self.octave_box.pack(side="left", padx=(4, 14))
        ttk.Label(settings, text="题数").pack(side="left")
        self.size_box = ttk.Combobox(
            settings, values=SESSION_CHOICES, state="readonly", width=5
        )
        self.size_box.set("10")
        self.size_box.pack(side="left", padx=(4, 0))
        self.auto_next = tk.BooleanVar(value=True)
        ttk.Checkbutton(settings, text="自动下一题", variable=self.auto_next).pack(
            side="left", padx=(16, 0)
        )
        for box in (self.scale_box, self.octave_box):
            box.bind("<<ComboboxSelected>>", lambda _e: self._on_settings_change())

        play_row = ttk.Frame(self)
        play_row.pack(fill="x", **pad)
        self.play_btn = tk.Button(
            play_row,
            text="▶  开始练习",
            command=self.start_session,
            font=("", 14, "bold"),
            bg="#2563eb",
            fg="white",
            activebackground="#1d4ed8",
            activeforeground="white",
            relief="flat",
            padx=16,
            pady=5,
            cursor="hand2",
        )
        self.play_btn.pack(side="left", fill="x", expand=True)
        ttk.Button(play_row, text="⏮ 上一题", width=9, command=self.prev_question).pack(
            side="left", padx=(10, 0)
        )
        ttk.Button(play_row, text="⏭ 下一题", width=9, command=self.next_question).pack(
            side="left", padx=(6, 0)
        )
        self.mistake_btn = ttk.Button(
            play_row, width=10, command=lambda: self.start_session(True)
        )
        self.mistake_btn.pack(side="left", padx=(6, 0))
        ttk.Button(
            play_row, text="🙈 看答案", width=9, command=self.reveal_answer
        ).pack(side="left", padx=(6, 0))
        self._update_mistake_button()

        # 音名按钮: 按当前调性列出全部音, 点击即作答
        self.note_buttons: dict[str, tk.Button] = {}
        self.answer_grid = ttk.Frame(self)
        self.answer_grid.pack(fill="x", **pad)
        self._render_answer_buttons()

        self.feedback = ttk.Label(
            self,
            text="选好调性、八度和题数，点「开始练习」🎧",
            font=("", 13),
            foreground=GRAY,
            wraplength=430,
            justify="center",
        )
        self.feedback.pack(fill="x", padx=18, pady=(10, 2))

        self.stats_var = tk.StringVar()
        ttk.Label(self, textvariable=self.stats_var, foreground=GRAY).pack()
        self._render_stats()

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=18, pady=(2, 8))
        self.chips = ttk.Frame(bottom)
        self.chips.pack(side="left")
        self.progress_label = ttk.Label(bottom, text="", foreground=GRAY)
        self.progress_label.pack(side="right")

        ttk.Label(
            self,
            foreground="#9ca3af",
            text="空格 = 下一题 · 点击音名直接作答 · 答对答错都会自动进入下一题",
        ).pack(pady=(0, 10))

        self.bind("<space>", self._on_space)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _render_answer_buttons(self, names: list[str] | None = None) -> None:
        """重建音名按钮(每行 6 个, 点击即作答); 默认用当前调性, 错题练习用错题集."""
        if names is None:
            names = SCALES[self.scale_box.get()]
        for btn in self.note_buttons.values():
            btn.destroy()
        self.note_buttons.clear()
        for i, name in enumerate(names):
            row, col = divmod(i, 6)
            btn = tk.Button(
                self.answer_grid,
                text=display_name(name),
                command=lambda n=name: self.answer(n),
                font=("", 13, "bold"),
                bg=NOTE_BG,
                fg=DARK,
                activebackground=NOTE_BG_ACTIVE,
                activeforeground=DARK,
                relief="flat",
                padx=10,
                pady=4,
                cursor="hand2",
            )
            btn.grid(row=row, column=col, padx=3, pady=3, sticky="nsew")
            self.note_buttons[name] = btn
        for col in range(6):
            self.answer_grid.columnconfigure(col, weight=1)

    # ---- 练习会话 ----

    def _selected_octave(self) -> int:
        value = self.octave_box.get()
        if value == "随机":
            return random.randint(OCTAVE_RANGE.start, OCTAVE_RANGE.stop - 1)
        return int(value)

    def start_session(self, from_mistakes: bool = False) -> None:
        """开始一组练习: 预生成整组题目; from_mistakes 时只考错题集里的音."""
        self._cancel_auto_next()
        if from_mistakes:
            names = [name for _pc, (name, _n) in sorted(self.mistakes.items())]
            if not names:
                self._set_feedback("错题集是空的；答错、跳过或看答案的音会自动收进来", GRAY)
                return
        else:
            names = SCALES[self.scale_box.get()]
        count = int(self.size_box.get())
        questions: list[tuple[str, int]] = []
        previous: str | None = None
        for _ in range(count):
            choices = [n for n in names if n != previous] or names
            previous = random.choice(choices)
            questions.append((previous, note_midi(previous, self._selected_octave())))
        self.questions = questions
        self.results = [None] * count
        self.q_index = 0
        self.session_active = True
        self.mistake_session = from_mistakes
        self._render_answer_buttons(names)
        self._enter_question(0)

    def _enter_question(self, index: int) -> None:
        """进入第 index 题(未答: 出题播放; 已答: 复盘展示当时的反馈)."""
        self.q_index = index
        name, midi = self.questions[index]
        self.expected_name, self.expected_midi = name, midi
        for btn in self.note_buttons.values():
            btn.configure(bg=NOTE_BG, fg=DARK)  # 复位上一题的对错配色
        self.play_btn.configure(text="🔁  重听", command=self.replay)
        recorded = self.results[index]
        if recorded is None:
            self._set_feedback(
                f"仔细听……第 {index + 1}/{len(self.questions)} 题，这是什么音？", DARK
            )
        else:
            ok, clicked, message = recorded
            self._apply_result_colors(ok, clicked)
            self._set_feedback(message, GREEN if ok else RED)
        try:
            self.audio.play(midi)
        except RuntimeError as err:
            self._set_feedback(f"播放失败：{err}", RED)
            return
        self._render_stats()

    def replay(self) -> None:
        """重听当前题(与「开始练习」合用一个按钮, 随会话状态切换)."""
        if not self.session_active:
            self._set_feedback("还没有题目，点「开始练习」", GRAY)
            return
        self.audio.play(self.questions[self.q_index][1])

    def prev_question(self) -> None:
        """回看上一题(已答的题只复盘, 不能重答)."""
        if not self.session_active:
            self._set_feedback("还没有题目，点「开始练习」", GRAY)
            return
        self._cancel_auto_next()
        if self.q_index == 0:
            self._set_feedback("已经是第一题了", GRAY)
            return
        self._enter_question(self.q_index - 1)

    def next_question(self) -> None:
        """下一题; 当前题未作答则跳过(计为错误), 最后一题之后出总结."""
        if not self.session_active:
            self._set_feedback("还没有题目，点「开始练习」", GRAY)
            return
        self._cancel_auto_next()
        if self.results[self.q_index] is None:
            self._forfeit("⏭ 已跳过，正确答案")
            return
        if self.q_index + 1 >= len(self.questions):
            self._show_summary()
            return
        self._enter_question(self.q_index + 1)

    def answer(self, name: str) -> None:
        """点击音名按钮作答: 直接判断正误, 无需提交."""
        if not self.session_active or self.results[self.q_index] is not None:
            self._set_feedback("这题已经作答过了，点「下一题」继续", GRAY)
            return
        pc = parse_note_name(name)[0]
        shown = self._shown_note()
        if pc == self.expected_midi % 12:
            self.correct += 1
            self.streak += 1
            self.best_streak = max(self.best_streak, self.streak)
            message = f"✅ 正确！是 {shown}（唱名 {solfege(self.expected_name)}）"
            if self.streak >= 3:
                message += f"　🔥 连对 {self.streak}"
            expected_pc = self.expected_midi % 12
            if expected_pc in self.mistakes:
                del self.mistakes[expected_pc]
                save_mistakes(self.mistakes_file, self.mistakes)
                self._update_mistake_button()
                message += "　🎉 已移出错题集"
            self.results[self.q_index] = (True, name, message)
            self._apply_result_colors(True, name)
            self._finish(True, message, GREEN)
            if self.auto_next.get():
                self._auto_after = self.after(900, self._deferred_next)
        else:
            message = (
                f"❌ 不是 {display_name(name)}，正确答案是 {shown}"
                f"（唱名 {solfege(self.expected_name)}）。点「重听」加深印象 🎧"
            )
            self._add_mistake()
            self.results[self.q_index] = (False, name, message)
            self._apply_result_colors(False, name)
            self._finish(False, message, RED)
            if self.auto_next.get():  # 答错也直接进入下一题, 多留一点时间看答案
                self._auto_after = self.after(1400, self._deferred_next)

    def reveal_answer(self) -> None:
        """直接看答案(实在听不出来时用, 计为一次错误作答)."""
        if not self.session_active or self.results[self.q_index] is not None:
            self._set_feedback("这题已经作答过了，点「下一题」继续", GRAY)
            return
        self._forfeit("🙈 答案")

    def _forfeit(self, prefix: str) -> None:
        """未作答直接揭晓(看答案 / 跳过): 计为错误并记入错题集."""
        message = (
            f"{prefix}是 {self._shown_note()}"
            f"（唱名 {solfege(self.expected_name)}），下一题加油"
        )
        self._add_mistake()
        self.results[self.q_index] = (False, None, message)
        self._apply_result_colors(False, None)
        self._finish(False, message, GRAY)
        if self.auto_next.get():
            self._auto_after = self.after(1400, self._deferred_next)

    def _show_summary(self) -> None:
        """一组练习结束: 统计本轮答对 / 答错哪些音."""
        self.session_active = False
        self._cancel_auto_next()
        total = len(self.questions)
        right = [name for (name, _m), r in zip(self.questions, self.results) if r[0]]
        wrong = [name for (name, _m), r in zip(self.questions, self.results) if not r[0]]
        lines = [
            (
                f"🎉 练习完成！本轮 {total} 题答对 {len(right)} 题"
                f"（{round(100 * len(right) / total)}%）"
            )
        ]
        if right:
            lines.append("✅ 答对：" + "、".join(display_name(n) for n in dict.fromkeys(right)))
        if wrong:
            parts = [
                f"{display_name(n)}×{wrong.count(n)}" if wrong.count(n) > 1 else display_name(n)
                for n in dict.fromkeys(wrong)
            ]
            lines.append(f"❌ 答错：{'、'.join(parts)}（可点「错题」巩固）")
        else:
            lines.append("全部答对，太棒了！")
        self._set_feedback("\n".join(lines), DARK)
        self.play_btn.configure(text="▶  再来一组", command=self.start_session)
        self._render_stats()

    def _finish(self, ok: bool, message: str, color: str) -> None:
        """结束当前题: 记入统计与历史, 显示反馈."""
        self.attempts += 1
        if not ok:
            self.streak = 0  # 答错或看答案都会打断连对
        self.history.append((self._shown_note(), ok))
        del self.history[:-12]  # 只保留最近 12 条
        self._set_feedback(message, color)
        self._render_stats()
        self._render_chips()

    # ---- 小工具 ----

    def _shown_note(self) -> str:
        """当前题的展示写法, 如 "F♯4"."""
        return f"{display_name(self.expected_name)}{(self.expected_midi // 12) - 1}"

    def _set_feedback(self, text: str, color: str) -> None:
        self.feedback.configure(text=text, foreground=color)

    def _apply_result_colors(self, ok: bool, clicked: str | None) -> None:
        """按作答结果给音名按钮上色: 正确答案绿色, 点错的选项红色."""
        self.note_buttons[self.expected_name].configure(bg=GREEN, fg="white")
        if not ok and clicked and clicked != self.expected_name:
            self.note_buttons[clicked].configure(bg=RED, fg="white")

    def _add_mistake(self) -> None:
        """把当前题的音记入错题集(按音高合并, 等音写法共用一条)并保存."""
        pc = self.expected_midi % 12
        name, count = self.mistakes.get(pc, (self.expected_name, 0))
        self.mistakes[pc] = (name, count + 1)
        save_mistakes(self.mistakes_file, self.mistakes)
        self._update_mistake_button()

    def _update_mistake_button(self) -> None:
        count = len(self.mistakes)
        self.mistake_btn.configure(
            text=f"🎯 错题 ({count})", state="normal" if count else "disabled"
        )

    def _deferred_next(self) -> None:
        self._auto_after = None
        self.next_question()

    def _cancel_auto_next(self) -> None:
        if self._auto_after is not None:
            self.after_cancel(self._auto_after)
            self._auto_after = None

    def _on_settings_change(self) -> None:
        self._cancel_auto_next()
        self.session_active = False  # 切换调性/八度会作废进行中的一组
        self._render_answer_buttons()
        self.play_btn.configure(text="▶  开始练习", command=self.start_session)
        self._set_feedback(
            f"已切换：{self.scale_box.get()} · 八度 {self.octave_box.get()}，点「开始练习」开始",
            GRAY,
        )
        self._render_stats()

    def _render_stats(self) -> None:
        head = (
            f"第 {self.q_index + 1}/{len(self.questions)} 题 · "
            if self.session_active
            else ""
        )
        accuracy = round(100 * self.correct / self.attempts) if self.attempts else 0
        self.stats_var.set(
            f"{head}已答 {self.attempts} · 正确 {self.correct} · 准确率 {accuracy}% "
            f"· 连对 {self.streak}（最佳 {self.best_streak}）"
        )

    def _render_chips(self) -> None:
        for chip in self.chips.winfo_children():
            chip.destroy()
        for text, ok in self.history:
            ttk.Label(
                self.chips,
                text=f"{text} {'✓' if ok else '✗'}",
                foreground=GREEN if ok else RED,
            ).pack(side="left", padx=2)

    def _poll_preload(self) -> None:
        """轮询后台音源预热的进度(避免跨线程直接碰 tkinter)."""
        try:
            while True:
                item = self._progress_q.get_nowait()
                if item is None:
                    self.progress_label.configure(text="")
                    return
                done, total = item
                self.progress_label.configure(text=f"音源准备中 {done}/{total}")
        except queue.Empty:
            pass
        self.after(150, self._poll_preload)

    # ---- 按键/关闭 ----

    def _on_space(self, event: tk.Event) -> None:
        focused = self.focus_get()
        if isinstance(focused, (ttk.Button, tk.Button, ttk.Checkbutton)):
            return  # 交给控件默认行为
        if self.session_active:
            self.next_question()
        else:
            self.start_session()

    def _on_close(self) -> None:
        self.audio.stop()
        self.destroy()


def main() -> None:
    """程序入口."""
    EarTrainer().mainloop()


if __name__ == "__main__":
    main()
