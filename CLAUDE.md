# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

音准练习 — a tkinter ear-training app for absolute pitch: plays a random note from the selected key × octave, the user types the note name, and gets feedback and stats. All UI text, comments, and docstrings are in Chinese; write new ones in Chinese too.

## Commands

Run the app (uses the uv-managed project venv, which includes numpy for faster synthesis):

    uv run python src/tone_practice.py

Preview the static web version (or just open `docs/index.html` directly in a browser):

    python3 -m http.server -d docs

Smoke check (assert-based, plays no sound, briefly opens a Tk window). It must run as a module from the repo root because it does `import src.tone_practice` — invoking the file by path fails with `ModuleNotFoundError`:

    uv run python -m src.smoke_check

Success prints `smoke: ALL OK`. There is no test framework, linter, or formatter configured. (The README's `python3 tone_practice.py` / `python3 smoke_check.py` paths predate the `src/` layout.)

## Architecture

Everything lives in [src/tone_practice.py](src/tone_practice.py) (~630 lines), layered top-to-bottom with `# ====` section markers:

1. **Music theory** — `parse_note_name()` leniently parses user input (`C` / `c` / `f#` / `f♯` / `Bb` / `bB` / `♭B` / `升F` / `降B` …) into `(pitch_class, canonical_name)`. The `SCALES` dict maps each key to its note names, which defines the canonical spelling of answers: enharmonic inputs count as correct, and the app shows the in-key spelling in feedback.
2. **Piano synthesis** — `synthesize()` does additive synthesis (partials + inharmonicity + exponential decay envelope) with two implementations: `_synth_numpy` and `_synth_pure`. numpy is imported in a `try/except` and is optional at runtime even though it is a declared dependency — the pure-Python fallback must keep working. The active path also determines `RATE` and `PARTIALS`; `RATE` is part of WAV cache filenames, so caches from the two paths don't collide. `PianoVoice` synthesizes to WAV cached under `$TMPDIR/tone_practice_piano` (atomic `os.replace`), then plays it via a system player: `afplay` (macOS), `winsound` (Windows), `aplay`/`paplay` (Linux). `preload()` runs in a daemon thread at startup and reports progress through a `queue.Queue` that the Tk main loop polls in `_poll_preload` — tkinter widgets are only ever touched from the main thread.
3. **GUI** — `EarTrainer(tk.Tk)`: practice sessions. `start_session()` generates all N questions upfront (`题数` setting) from the scale (or from the mistake set with `from_mistakes=True`); `answer()` / `reveal_answer()` / skipping via `next_question()` all record into `self.results`, then auto-advance via `self.after(...)` (correct and wrong alike; `_cancel_auto_next` guards pending callbacks). `prev_question()`/`next_question()` navigate — already-answered questions reopen in review mode (`_enter_question`) and cannot be re-answered; after the last one, `_show_summary()` reports which notes were right/wrong. The play button doubles as replay (label/command swap on session state). `_finish` records global stats and the last-12 history chips. The 错题集 (mistake set) is keyed by pitch class and persisted as JSON at `~/.tone_practice_mistakes.json` (`load_mistakes`/`save_mistakes`; wrong/skip/reveal adds, one correct answer removes). Space = next question (start when idle).

`src/main.py` is unused `uv init` scaffolding — the real entry point is `src/tone_practice.py` (`main()` at the bottom).

**Web version** — [docs/index.html](docs/index.html) is a fully static, backend-free single page hosted on GitHub Pages (Settings → Pages → main branch / `docs`). It is a hand-ported mirror of the tkinter `EarTrainer` *and* of the piano synthesis: the additive-synthesis constants and functions (`noteShape` / `partialParams` / `synthNote`) mirror `_note_shape` / `_partial_params` / `_synth_pure`, rendered into `AudioBuffer`s via Web Audio. The page has two tab-switched views (`makeView`), 日常练习 and 错题练习, each holding its own independent session (questions/results/qIndex/autoTimer — switching tabs cancels the leaving view's pending auto-advance but preserves its session); stats, history, and the localStorage mistake set are shared between them. The JS `SCALES` / `OCTAVE_CHOICES` / `SESSION_CHOICES` literals must stay identical to the Python ones — `smoke_check.py` extracts them by regex and asserts equality, so change both sides together.

[src/smoke_check.py](src/smoke_check.py) drives the real `EarTrainer` widget with `audio.play` stubbed out and a temp `mistakes_file`, plus `parse_note_name`, WAV-length, and web-page data-consistency assertions. Extend it when changing parsing, scoring, sessions, or synthesis.
