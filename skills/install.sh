#!/bin/bash
# Install Sutando skills into Claude Code (~/.claude/skills/)
# Creates symlinks so updates to the repo are picked up automatically.

set -e

SKILLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SKILLS_DIR")"
TARGET="$HOME/.claude/skills"

mkdir -p "$TARGET"

install_from() {
  local src_root="$1"
  [ ! -d "$src_root" ] && return 0
  for skill_dir in "$src_root"/*/; do
    [ ! -d "$skill_dir" ] && continue
    local skill_name
    skill_name=$(basename "$skill_dir")
    [ "$skill_name" = "install.sh" ] && continue
    [ ! -f "$skill_dir/SKILL.md" ] && continue

    if [ -L "$TARGET/$skill_name" ] && [ ! -e "$TARGET/$skill_name" ]; then
      rm "$TARGET/$skill_name"
      ln -s "$skill_dir" "$TARGET/$skill_name"
      echo "  ✓ $skill_name (relinked — old symlink was broken)"
    elif [ -L "$TARGET/$skill_name" ]; then
      echo "  ↻ $skill_name (symlink exists)"
    elif [ -d "$TARGET/$skill_name" ]; then
      echo "  ⚠ $skill_name (directory exists, skipping — remove manually to reinstall)"
    else
      ln -s "$skill_dir" "$TARGET/$skill_name"
      echo "  ✓ $skill_name"
    fi
  done
}

install_from "$SKILLS_DIR"
if [ -n "$SUTANDO_PRIVATE_DIR" ]; then
  install_from "${SUTANDO_PRIVATE_DIR/#\~/$HOME}/skills"
fi

echo ""
echo "Installed. Skills available in any Claude Code session."
