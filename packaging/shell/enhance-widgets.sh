#!/usr/bin/env bash
# Shell widgets: enhance the current command-line buffer in place.
# Source this from your ~/.bashrc or ~/.zshrc:   source /path/to/enhance-widgets.sh
# Then press Ctrl-X Ctrl-E to rewrite whatever you've typed.

_enhance_buffer() {
  local out
  out="$(enhance-cli --no-clipboard -y "$1" 2>/dev/null)" || return 1
  printf '%s' "$out"
}

if [ -n "$ZSH_VERSION" ]; then
  enhance-widget() {
    local rewritten
    rewritten="$(_enhance_buffer "$BUFFER")" || return
    [ -n "$rewritten" ] && BUFFER="$rewritten" && CURSOR=${#BUFFER}
  }
  zle -N enhance-widget
  bindkey '^X^E' enhance-widget
elif [ -n "$BASH_VERSION" ]; then
  enhance-widget() {
    local rewritten
    rewritten="$(_enhance_buffer "$READLINE_LINE")" || return
    [ -n "$rewritten" ] && READLINE_LINE="$rewritten" && READLINE_POINT=${#READLINE_LINE}
  }
  bind -x '"\C-x\C-e": enhance-widget' 2>/dev/null
fi
