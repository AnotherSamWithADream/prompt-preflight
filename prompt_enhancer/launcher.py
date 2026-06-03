"""``enhance`` -- enhance your first prompt and launch an interactive Claude Code session.

    enhance "make my code faster and add tests"

…rewrites that prompt with Haiku and starts an **interactive** ``claude`` whose first
message is the enhanced prompt. A local proxy also runs, so follow-up prompts you type in
the session are enhanced too (the first prompt is skipped so it isn't enhanced twice).
When ``claude`` exits, the proxy stops.

Argument handling -- ``enhance [-q] [-m TEXT] [PROMPT...] [CLAUDE FLAGS...] [-- CLAUDE ARGS...]``:

* ``enhance <words...>``               -> the words are your prompt (enhanced, then sent)
* ``enhance -m "<prompt>"``            -> explicit prompt (robust for prompts starting with -)
* ``enhance <prompt> --model opus``    -> leading words = prompt; trailing flags go to claude
* ``enhance <prompt> -- <claude args>``-> everything after ``--`` is passed to claude verbatim
* ``enhance -- <claude args>``         -> no prompt; launch claude through the proxy with those args
* ``enhance``                          -> plain interactive claude through the proxy
* ``enhance --serve-only [...]``       -> run only the proxy

Pass any ``claude`` parameters either as trailing flags or, to be unambiguous, after ``--``
(e.g. ``enhance "fix the bug" -- --model opus --permission-mode plan --add-dir ./src``).
Persistent defaults can be set with the ``claude_args`` config field /
``PROMPT_ENHANCER_CLAUDE_ARGS`` (CLI args are appended after them, so they take precedence).

Launcher options: ``-m/--message <text>``, ``-q/--quiet`` (don't echo the rewrite). A
``//raw`` prefix or a leading ``/`` (slash command) on the prompt skips enhancement.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading

from prompt_enhancer.config import load_config
from prompt_enhancer.engine import enhance, resolve_claude_binary
from prompt_enhancer.policy import strip_raw
from prompt_enhancer.proxy import inherit_upstream, make_server

# Backwards-compatible alias (some tests/imports use the private name).
_resolve_claude_binary = resolve_claude_binary


def _interactive_capable() -> bool:
    try:
        return sys.stdout.isatty()
    except Exception:  # noqa: BLE001
        return False


def parse_invocation(args):
    """Parse ``enhance`` arguments into ``(quiet, message, prompt, claude_args)``.

    Grammar::

        enhance [-q|--quiet] [-m TEXT|--message TEXT] [PROMPT...] [CLAUDE FLAGS...] [-- CLAUDE ARGS...]

    * Launcher options (``-q``/``--quiet``, ``-m``/``--message``) are recognised ONLY in the
      leading run -- before any prompt word, claude flag, or ``--``. (So a ``-m`` meant for
      claude, placed after ``--``, is never mistaken for the launcher's own option.)
    * The prompt is the ``-m`` value, or otherwise the leading non-dash words.
    * Everything else -- trailing flags, **and everything after a ``--`` separator** -- is
      forwarded to ``claude`` verbatim, so you can pass any claude parameter (even one that
      would otherwise collide with an ``enhance`` option) after ``--``.
    """
    args = list(args)
    quiet = False
    message = None

    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-q", "--quiet"):
            quiet = True
            i += 1
        elif a in ("-m", "--message"):
            message = args[i + 1] if i + 1 < len(args) else ""
            i += 2
        elif a.startswith("--message="):
            message = a.split("=", 1)[1]
            i += 1
        else:
            break

    rest = args[i:]
    passthrough: list = []
    if "--" in rest:
        k = rest.index("--")
        passthrough = rest[k + 1 :]
        rest = rest[:k]

    prompt_words: list = []
    if message is None:
        while rest and not rest[0].startswith("-"):
            prompt_words.append(rest.pop(0))

    prompt = message if message is not None else " ".join(prompt_words).strip()
    return quiet, message, prompt, rest + passthrough


def _resolve_initial_prompt(prompt: str, cfg):
    """Turn the raw prompt into the text to hand claude. Honors ``//raw`` and slash
    commands; otherwise enhances. Always safe (fails open). Returns ``(text, note)``."""
    stripped = prompt.strip()
    if stripped.startswith(cfg.bypass_prefix):
        return strip_raw(
            stripped, cfg.bypass_prefix
        ), f"({cfg.bypass_prefix}) using your prompt as-is"
    if stripped.startswith("/"):
        return prompt, "slash command -- passing through unchanged"
    result = enhance(prompt, config=cfg)
    if result.enhanced:
        return result.text, f"rewrote your prompt ({len(prompt)} -> {len(result.text)} chars)"
    return result.text, f"enhancement skipped ({result.error}); using your prompt as-is"


def build_child_env(base_env: dict, host: str, port: int):
    """Env for the child ``claude``: route it through the proxy, and tell our own
    components exactly where the proxy is."""
    base = f"http://{host}:{port}"
    env = dict(base_env)
    env["ANTHROPIC_BASE_URL"] = base
    env["PROMPT_ENHANCER_PROXY_HOST"] = host
    env["PROMPT_ENHANCER_PROXY_PORT"] = str(port)
    return env, base


def _bind_server(cfg, skip_texts):
    try:
        return make_server(cfg, skip_texts=skip_texts)
    except OSError:
        cfg.proxy_port = 0  # 0 -> OS picks an available ephemeral port
        return make_server(cfg, skip_texts=skip_texts)


def _print_prompt(initial: str) -> None:
    sys.stderr.write("\n".join("    " + line for line in initial.splitlines()) + "\n")


def launch(raw_args) -> int:
    cfg = load_config()
    inherit_upstream(cfg)  # forward to a corporate gateway if ANTHROPIC_BASE_URL is one
    quiet, _message, prompt, cli_claude_args = parse_invocation(raw_args)
    # Persistent defaults from config come first; CLI args are appended so they win.
    claude_flags = [*cfg.claude_args, *cli_claude_args]

    skip_texts = set()
    initial = None
    if prompt:
        initial, note = _resolve_initial_prompt(prompt, cfg)
        sys.stderr.write(f"enhance: {note}\n")
        if not quiet and initial and initial.strip() != prompt.strip():
            _print_prompt(initial)
        if initial and initial.strip():
            skip_texts.add(initial.strip())

    try:
        server = _bind_server(cfg, skip_texts)
    except ValueError as exc:  # bind-safety (non-loopback host)
        sys.stderr.write(f"enhance: {exc}\n")
        return 2
    except OSError as exc:
        sys.stderr.write(
            f"enhance: cannot start proxy on {cfg.proxy_host}:{cfg.proxy_port} ({exc})\n"
        )
        return 1

    port = server.server_address[1]
    env, base = build_child_env(dict(os.environ), cfg.proxy_host, port)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    claude_bin = resolve_claude_binary()
    claude_argv = [claude_bin, *claude_flags]
    # If stdout isn't a terminal (piped/redirected), an interactive TUI won't render --
    # fall back to print mode for a one-shot answer.
    if (
        initial is not None
        and not _interactive_capable()
        and not ({"-p", "--print"} & set(claude_flags))
    ):
        sys.stderr.write("enhance: stdout is not a terminal; running claude in print mode (-p).\n")
        claude_argv.append("-p")
    if initial is not None:
        claude_argv.append(initial)

    if claude_flags:
        sys.stderr.write(f"enhance: forwarding to claude: {' '.join(claude_flags)}\n")
    sys.stderr.write(
        f"enhance: launching Claude Code (follow-up prompts are enhanced via {base}; "
        "exit claude to stop)\n"
    )
    sys.stderr.flush()

    code = 0
    try:
        code = subprocess.run(claude_argv, env=env).returncode
    except FileNotFoundError:
        sys.stderr.write(f"enhance: could not launch claude ('{claude_bin}').\n")
        code = 127
    except KeyboardInterrupt:
        code = 130
    finally:
        server.shutdown()
        server.server_close()
    return code


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--version" in args or "-V" in args:
        from prompt_enhancer import __version__

        sys.stdout.write(f"prompt-preflight {__version__}\n")
        return 0
    if args[:1] == ["--serve-only"]:
        from prompt_enhancer.proxy import main as proxy_main

        return proxy_main(args[1:])
    return launch(args)


if __name__ == "__main__":
    sys.exit(main())
