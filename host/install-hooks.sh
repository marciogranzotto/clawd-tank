#!/bin/bash
# install-hooks.sh — Installs Clawd notification hooks into Claude Code settings.
# Usage: ./install-hooks.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAWD_NOTIFY="$SCRIPT_DIR/clawd-notify"
SETTINGS_FILE="$HOME/.claude/settings.json"

if [ ! -f "$CLAWD_NOTIFY" ]; then
    echo "Error: clawd-notify not found at $CLAWD_NOTIFY"
    exit 1
fi

echo "Clawd notify path: $CLAWD_NOTIFY"
echo ""
echo "Add the following to $SETTINGS_FILE under the 'hooks' key:"
echo ""
cat <<EOF
{
  "hooks": {
    "Notification": [
      {
        "matcher": "idle_prompt",
        "hooks": [
          {
            "type": "command",
            "command": "$CLAWD_NOTIFY"
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "$CLAWD_NOTIFY"
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "$CLAWD_NOTIFY"
          }
        ]
      }
    ]
  }
}
EOF

echo ""
echo "NOTE: If you already have hooks configured, merge the above into your existing config."
echo "The hook config goes in ~/.claude/settings.json (NOT hooks.json)."
echo ""
echo "The 'matcher' field filters which notification types trigger the hook."
echo "If your Claude Code version doesn't support 'matcher', remove it —"
echo "clawd-notify already filters by notification_type in protocol.py."
