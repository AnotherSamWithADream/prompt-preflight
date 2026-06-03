"""``enhance-cli`` -- standalone CLI for the desktop-app workflow.

Reads a prompt (from arguments or stdin), runs the enhancement engine, shows a short
summary of the changes, lets you Accept / Edit / Reject, and copies the chosen text
to the system clipboard (pbcopy / clip / wl-copy / xclip / xsel) for pasting into the
Claude desktop or web app.

The final chosen text is written to **stdout**; all UI (preview, summary, prompts)
goes to **stderr**, so ``enhance "..." | something`` stays clean.

Privacy: prompt contents are never logged. The Edit action writes to a temporary file
(so your editor can open it) which is deleted immediately afterwards.

The engine sets ``PROMPT_ENHANCER_ACTIVE=1`` when it calls ``claude -p``, so if the
global UserPromptSubmit hook is installed the prompt is not enhanced twice.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import subprocess
import sys
import tempfile
from shutil import which

# Allow running directly without installing the package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prompt_enhancer.config import (  # noqa: E402
    config_path,
    load_config,
    to_dict,
    user_config_path,
    write_template,
)
from prompt_enhancer.engine import enhance  # noqa: E402

RAW_PREFIX = "//raw"


# --------------------------------------------------------------------------- #
# //raw handling                                                              #
# --------------------------------------------------------------------------- #


def strip_raw_prefix(text: str, prefix: str = RAW_PREFIX) -> tuple:
    """If ``text`` begins with the bypass token, return ``(stripped, True)``.

    Unlike the hook, the CLI owns its own output, so it can physically remove the
    token before printing/copying.
    """
    lead = text.lstrip()
    if lead == prefix:
        return "", True
    for sep in (" ", "\t", "\n", "\r"):
        if lead.startswith(prefix + sep):
            return lead[len(prefix) :].lstrip(), True
    return text, False


# --------------------------------------------------------------------------- #
# Clipboard                                                                    #
# --------------------------------------------------------------------------- #


def _clipboard_command() -> list[str] | None:
    if sys.platform == "darwin":
        return ["pbcopy"]
    if sys.platform.startswith("win"):
        return ["clip"]
    for cmd in (
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
    ):
        if which(cmd[0]):
            return cmd
    return None


def _win_set_clipboard(text: str) -> bool:
    """Set the Windows clipboard via the Win32 API (CF_UNICODETEXT) so non-ASCII text is
    preserved -- unlike ``clip``, which mangles it through the console code page."""
    import ctypes

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    windll = ctypes.windll  # type: ignore[attr-defined]  # Windows-only API
    u32 = windll.user32
    k32 = windll.kernel32
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    k32.GlobalFree.argtypes = [ctypes.c_void_p]
    u32.SetClipboardData.restype = ctypes.c_void_p
    u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

    data = text.encode("utf-16-le") + b"\x00\x00"
    if not u32.OpenClipboard(None):
        return False
    try:
        u32.EmptyClipboard()
        handle = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            return False
        ptr = k32.GlobalLock(handle)
        if not ptr:
            k32.GlobalFree(handle)
            return False
        ctypes.memmove(ptr, data, len(data))
        k32.GlobalUnlock(handle)
        if not u32.SetClipboardData(CF_UNICODETEXT, handle):
            k32.GlobalFree(handle)  # ownership not transferred -> free it
            return False
        return True  # success: the clipboard now owns the handle
    finally:
        u32.CloseClipboard()


def copy_to_clipboard(text: str) -> bool:
    """Copy ``text`` to the system clipboard. Returns True on success."""
    if sys.platform.startswith("win"):
        try:
            if _win_set_clipboard(text):
                return True
        except Exception:  # noqa: BLE001 -- fall back to `clip`
            pass
    cmd = _clipboard_command()
    if not cmd:
        return False
    try:
        proc = subprocess.run(cmd, input=text, text=True, encoding="utf-8")
        return proc.returncode == 0
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# Change summary                                                               #
# --------------------------------------------------------------------------- #


def _is_numbered(line: str) -> bool:
    s = line.strip()
    return len(s) >= 2 and s[0].isdigit() and s[1] in ".)"


def _has_list(text: str) -> bool:
    return any(
        ln.strip().startswith(("-", "*", "•")) or _is_numbered(ln) for ln in text.splitlines()
    )


def _count_open_questions(text: str) -> int:
    count, seen = 0, False
    for line in text.splitlines():
        if "open questions" in line.lower():
            seen = True
            continue
        if seen and line.strip().startswith(("-", "*", "•")):
            count += 1
    return count


#: Cap inputs to the O(n^2) diff so very long prompts don't stall the summary.
_DIFF_CAP = 20_000


def summarize_changes(original: str, enhanced: str) -> str:
    """A short, locally-computed (no extra model call) summary of what changed."""
    o_words, e_words = len(original.split()), len(enhanced.split())
    ratio = difflib.SequenceMatcher(a=original[:_DIFF_CAP], b=enhanced[:_DIFF_CAP]).ratio()
    lines = [f"  - Length: {o_words} -> {e_words} words"]

    if "open questions" in enhanced.lower():
        n = _count_open_questions(enhanced)
        plural = "s" if n != 1 else ""
        lines.append(f'  - Appended an "Open questions" section ({n} item{plural})')

    if _has_list(enhanced) and not _has_list(original):
        lines.append("  - Added structure (lists / line breaks)")

    if ratio > 0.95:
        lines.append("  - Wording left essentially unchanged")
    else:
        pct = round((1 - ratio) * 100)
        lines.append(f"  - Reworded for clarity (~{pct}% changed); your specifics preserved")
    return "\n".join(lines)


def _unified_diff(original: str, enhanced: str) -> str:
    diff = difflib.unified_diff(
        original.splitlines(),
        enhanced.splitlines(),
        fromfile="original",
        tofile="enhanced",
        lineterm="",
    )
    return "\n".join(diff)


# --------------------------------------------------------------------------- #
# Interactive bits                                                             #
# --------------------------------------------------------------------------- #


def _open_tty():
    """A readable file connected to the controlling terminal, or None."""
    try:
        return open("CONIN$") if os.name == "nt" else open("/dev/tty")
    except OSError:
        return None


def _prompt_line(message: str) -> str | None:
    """Print ``message`` to stderr and read one line from the terminal.

    Returns None when there is no interactive terminal at all (fully piped).
    """
    sys.stderr.write(message)
    sys.stderr.flush()
    if sys.stdin.isatty():
        return sys.stdin.readline()
    tty = _open_tty()
    if tty is None:
        return None
    with tty:
        return tty.readline()


def _edit_text(text: str) -> str | None:
    """Open ``text`` in the user's editor and return the edited result (or None)."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not editor:
        editor = "notepad" if os.name == "nt" else "nano"
    fd, path = tempfile.mkstemp(prefix="enhance-", suffix=".txt", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        try:
            subprocess.run([editor, path])
        except OSError:
            sys.stderr.write(f"enhance: could not launch editor '{editor}'.\n")
            return None
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    finally:
        try:
            os.remove(path)  # do not leave prompt contents on disk
        except OSError:
            pass


def _choose(enhanced: str) -> str | None:
    """Accept / Edit / Reject loop. Returns the chosen text, or None to reject."""
    while True:
        line = _prompt_line("\n[A]ccept / [E]dit / [R]eject (default: Accept)? ")
        if line is None:
            sys.stderr.write("(no interactive terminal: accepting)\n")
            return enhanced
        choice = line.strip().lower()
        if choice in ("", "a", "accept"):
            return enhanced
        if choice in ("e", "edit"):
            edited = _edit_text(enhanced)
            if edited:
                return edited
            sys.stderr.write("(edit cancelled / empty; choose again)\n")
            continue
        if choice in ("r", "reject"):
            return None
        sys.stderr.write("Please enter A, E, or R.\n")


# --------------------------------------------------------------------------- #
# Top level                                                                    #
# --------------------------------------------------------------------------- #


def _read_input(args: argparse.Namespace) -> str:
    if args.prompt:
        return " ".join(args.prompt)
    if sys.stdin.isatty():
        hint = "Ctrl+Z then Enter" if os.name == "nt" else "Ctrl+D"
        sys.stderr.write(f"Enter your prompt, then press {hint}:\n")
    return sys.stdin.read()


def _finish(text: str, args: argparse.Namespace) -> None:
    if not args.no_clipboard:
        if copy_to_clipboard(text):
            sys.stderr.write("Copied to clipboard.\n")
        else:
            sys.stderr.write("(Clipboard tool unavailable; the text is on stdout below.)\n")
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")


def _emit_json(original: str, text: str, result) -> int:
    """Emit a structured result to stdout (for scripting)."""
    obj = {
        "original": original,
        "enhanced": text,
        "changed": text.strip() != original.strip(),
    }
    if result is not None:
        obj["did_enhance"] = result.enhanced
        obj["backend"] = result.backend
        obj["elapsed"] = round(result.elapsed, 3)
        obj["error"] = result.error
    json.dump(obj, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="enhance-cli",
        description="Rewrite a rough prompt into a clearer one and copy it to the clipboard.",
    )
    parser.add_argument("prompt", nargs="*", help="Prompt text. If omitted, read from stdin.")
    parser.add_argument(
        "-y", "--yes", action="store_true", help="Accept the enhanced prompt without prompting."
    )
    parser.add_argument(
        "--no-clipboard",
        action="store_true",
        help="Do not touch the clipboard; just print to stdout.",
    )
    parser.add_argument(
        "--diff", action="store_true", help="Also show a unified diff of the changes."
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit a JSON result and exit (no prompt, no clipboard)."
    )
    args = parser.parse_args(argv)
    cfg = load_config()

    raw = _read_input(args)
    if not raw or not raw.strip():
        sys.stderr.write("enhance-cli: no input provided.\n")
        return 2

    # bypass -- skip enhancement, strip the token, use the text as-is.
    stripped, is_raw = strip_raw_prefix(raw, cfg.bypass_prefix)
    if is_raw:
        if args.json:
            return _emit_json(raw, stripped, None)
        sys.stderr.write(f"({cfg.bypass_prefix}) Enhancement skipped; using your text as-is.\n")
        _finish(stripped, args)
        return 0

    result = enhance(raw, config=cfg)
    if args.json:
        return _emit_json(raw, result.text, result)
    if not result.enhanced:
        sys.stderr.write(
            f"enhance-cli: enhancement skipped ({result.error or 'unavailable'}); "
            "using your text as-is.\n"
        )
        _finish(result.text, args)
        return 0

    enhanced = result.text
    sys.stderr.write("\n----- Enhanced prompt -----\n")
    sys.stderr.write(enhanced + "\n")
    sys.stderr.write("\nChanges made:\n" + summarize_changes(raw, enhanced) + "\n")
    if args.diff:
        sys.stderr.write("\n" + _unified_diff(raw, enhanced) + "\n")

    final: str | None
    if args.yes:
        final = enhanced
    else:
        final = _choose(enhanced)

    if final is None:
        sys.stderr.write("Rejected -- copying your original text instead.\n")
        final = raw
    _finish(final, args)
    return 0


def config_main(argv) -> int:
    """`enhance-cli config [show|path|init|edit|set]` -- view or manage the config file."""
    cmd = argv[0] if argv else "show"

    if cmd in ("show", "get", "list"):
        cfg = load_config()
        sys.stderr.write(f"# effective config (source: {config_path()})\n")
        # Provenance: note which keys are currently overridden by environment variables.
        env_over = [v for k, v in _config_env_map().items() if k in os.environ]
        if env_over:
            sys.stderr.write(f"# overridden by env: {', '.join(sorted(env_over))}\n")
        json.dump(to_dict(cfg), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    if cmd == "set":
        return _config_set(argv[1:])
    if cmd == "path":
        sys.stdout.write(config_path() + "\n")
        return 0
    if cmd == "init":
        path = user_config_path()
        if os.path.isfile(path):
            sys.stderr.write(f"config already exists: {path}\n")
        else:
            write_template(path)
            sys.stderr.write(f"wrote default config: {path}\n")
        return 0
    if cmd == "edit":
        path = user_config_path()
        if not os.path.isfile(path):
            write_template(path)
        editor = (
            os.environ.get("VISUAL")
            or os.environ.get("EDITOR")
            or ("notepad" if os.name == "nt" else "nano")
        )
        try:
            subprocess.run([editor, path])
        except OSError as exc:
            sys.stderr.write(f"could not open editor '{editor}': {exc}\n")
            return 1
        return 0

    sys.stderr.write("usage: enhance-cli config [show|path|init|edit|set <key> <value>]\n")
    return 2


def _config_env_map() -> dict:
    from prompt_enhancer.config import _ENV_MAP

    return _ENV_MAP


def _config_set(args) -> int:
    from dataclasses import fields as _fields

    from prompt_enhancer.config import Config, _coerce

    if len(args) < 2:
        sys.stderr.write("usage: enhance-cli config set <key> <value>\n")
        return 2
    key, value = args[0], args[1]
    field_names = {f.name for f in _fields(Config)}
    if key not in field_names:
        sys.stderr.write(
            f"unknown config key: {key}\n  valid keys: {', '.join(sorted(field_names))}\n"
        )
        return 2

    target = config_path()
    data: dict = {}
    if os.path.isfile(target):
        try:
            with open(target, encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            data = {}
    coerced = _coerce(key, value, getattr(Config(), key))
    data[key] = list(coerced) if isinstance(coerced, tuple) else coerced
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    sys.stderr.write(f"set {key} = {data[key]!r} in {target}\n")
    return 0


# --------------------------------------------------------------------------- #
# doctor -- diagnose binary / flags / auth / backend                          #
# --------------------------------------------------------------------------- #

MIN_CLAUDE_VERSION = (2, 1, 0)


def _parse_version(s: str) -> tuple:
    import re

    m = re.search(r"(\d+)\.(\d+)\.(\d+)", s or "")
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def _claude_version(binpath: str) -> str | None:
    try:
        proc = subprocess.run([binpath, "--version"], capture_output=True, text=True, timeout=15)
        return (proc.stdout or "").strip() or None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def doctor_main(argv) -> int:
    """`enhance-cli doctor` -- verify the binary, flags, auth, and backend work."""
    from prompt_enhancer import __version__
    from prompt_enhancer.config import validate
    from prompt_enhancer.engine import resolve_claude_binary

    w = sys.stderr.write
    cfg = load_config()
    ok = True

    w("prompt-preflight doctor\n")
    w(f"  version        : {__version__}\n")
    w(f"  config source  : {config_path()}\n")
    for problem in validate(cfg):
        ok = False
        w(f"  ! config error : {problem}\n")

    key_present = bool(os.environ.get(cfg.api_key_env))
    selected = "api" if (cfg.backend == "api" or (cfg.backend == "auto" and key_present)) else "cli"
    w(
        f"  backend        : {cfg.backend} -> {selected}  (API key {'present' if key_present else 'absent'})\n"
    )

    binpath = resolve_claude_binary()
    w(f"  claude binary  : {binpath}\n")
    ver = _claude_version(binpath)
    if ver:
        w(f"  claude version : {ver}\n")
        if _parse_version(ver) < MIN_CLAUDE_VERSION:
            w(
                f"  ! warning      : Claude Code >= {'.'.join(map(str, MIN_CLAUDE_VERSION))} recommended\n"
            )
    elif selected == "cli":
        ok = False
        w("  ! claude not found or not runnable (the cli backend needs it)\n")

    if "--no-call" not in argv:
        w("  live check     : enhancing a test prompt (spends a little usage)...\n")
        result = enhance(
            "please make this rough prompt much clearer and better for a stronger model", config=cfg
        )
        if result.enhanced:
            w(
                f"    -> OK ({result.backend}, {round(result.elapsed, 1)}s, {len(result.original)}->{len(result.text)} chars)\n"
            )
        else:
            ok = False
            w(f"    -> FAIL-OPEN ({result.error}); enhancement is NOT working\n")

    w("\n" + ("All checks passed." if ok else "Problems found (see above).") + "\n")
    return 0 if ok else 1


def main() -> int:
    argv = sys.argv[1:]
    if "--version" in argv or "-V" in argv:
        from prompt_enhancer import __version__

        sys.stdout.write(f"prompt-preflight (enhance-cli) {__version__}\n")
        return 0
    if argv and argv[0] == "config":
        return config_main(argv[1:])
    if argv and argv[0] == "doctor":
        return doctor_main(argv[1:])
    try:
        return run()
    except KeyboardInterrupt:
        sys.stderr.write("\nAborted.\n")
        return 130


if __name__ == "__main__":
    sys.exit(main())
