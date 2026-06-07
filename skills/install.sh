#!/bin/bash
# Install Sutando skills into Claude Code (~/.claude/skills/)
# Creates symlinks so updates to the repo are picked up automatically.

set -e

SKILLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$HOME/.claude/skills"

mkdir -p "$TARGET"

for skill_dir in "$SKILLS_DIR"/*/; do
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

echo ""
echo "Installed. Skills available in any Claude Code session."
