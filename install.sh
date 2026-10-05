#!/bin/sh
# install.sh - put this Agent Skill where your coding agent looks for skills.
#
# No single directory is read by every agent: Claude Code reads ~/.claude/skills
# and does NOT read ~/.agents/skills, while Cursor, Codex, Gemini CLI and Copilot
# read ~/.agents/skills and do not treat ~/.claude/skills as a primary location.
# Default here: one real copy in ~/.agents/skills, symlinked into ~/.claude/skills.
#
#   ./install.sh            user install (symlink into the Claude Code dir)
#   ./install.sh --copy     real copies in every target instead of symlinks
#   ./install.sh --project  install into ./.claude/skills and ./.agents/skills
#   ./install.sh --list     show targets and what exists, change nothing
#   ./install.sh --uninstall
set -eu

SRC=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
[ -f "$SRC/SKILL.md" ] || { echo "error: no SKILL.md next to install.sh" >&2; exit 1; }

# the spec requires the directory name to equal the frontmatter `name`
NAME=$(sed -n 's/^name:[[:space:]]*//p' "$SRC/SKILL.md" | head -n1)
[ -n "$NAME" ] || { echo "error: no name: in SKILL.md frontmatter" >&2; exit 1; }
case "$NAME" in
  *[!a-z0-9-]*) echo "error: name '$NAME' is not spec-legal (a-z 0-9 - only)" >&2; exit 1;;
esac

MODE=user; LINK=yes; ACTION=install
for a in "$@"; do
  case "$a" in
    --project) MODE=project ;;
    --user)    MODE=user ;;
    --copy)    LINK=no ;;
    --list)    ACTION=list ;;
    --uninstall) ACTION=uninstall ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $a (try --help)" >&2; exit 2 ;;
  esac
done

if [ "$MODE" = project ]; then
  PRIMARY="$PWD/.agents/skills"
  SECONDARY="$PWD/.claude/skills"
else
  PRIMARY="$HOME/.agents/skills"
  SECONDARY="$HOME/.claude/skills"
fi

say() { printf '%s\n' "$*"; }

if [ "$ACTION" = list ]; then
  say "skill    : $NAME"
  say "source   : $SRC"
  say "mode     : $MODE ($([ "$LINK" = yes ] && echo 'symlink' || echo 'copy'))"
  say ""
  say "would install to:"
  for t in "$PRIMARY" "$SECONDARY"; do
    if [ -e "$t/$NAME" ]; then st="already present"; else st="new"; fi
    say "  $t/$NAME   [$st]"
  done
  say ""
  say "other agents read different paths - see the table in README.md:"
  say "  Copilot personal : \$HOME/.copilot/skills"
  say "  Cursor personal  : \$HOME/.cursor/skills"
  say "  Gemini CLI       : \$HOME/.gemini/skills"
  say "  Codex system     : /etc/codex/skills"
  exit 0
fi

if [ "$ACTION" = uninstall ]; then
  for t in "$PRIMARY" "$SECONDARY"; do
    if [ -e "$t/$NAME" ] || [ -L "$t/$NAME" ]; then
      rm -rf -- "$t/$NAME"; say "removed  $t/$NAME"
    fi
  done
  exit 0
fi

install_to() {
  target=$1; want_link=$2
  mkdir -p -- "$target"
  dest="$target/$NAME"
  if [ -e "$dest" ] || [ -L "$dest" ]; then
    say "exists   $dest  (remove it first, or run --uninstall)"
    return 0
  fi
  if [ "$want_link" = yes ]; then
    ln -s -- "$SRC" "$dest" && say "linked   $dest -> $SRC"
  else
    cp -R -- "$SRC" "$dest"
    rm -rf -- "$dest/.git" "$dest/.tmp"
    say "copied   $dest"
  fi
}

# Primary gets a real copy when the source is elsewhere; a symlink is enough when
# the caller asked for links.
install_to "$PRIMARY" "$LINK"
install_to "$SECONDARY" "$LINK"

say ""
say "done. verify with:"
say "  ls -l $SECONDARY/$NAME"
say "  uv run $SECONDARY/$NAME/scripts/paper_checks.py probe"
say "then open a new agent session so the skill is discovered."
