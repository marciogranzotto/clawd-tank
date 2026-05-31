"""Resolve the long-lived Claude Code session PID by walking the process tree.

Mirrors claude-plugins/agent-bus/lib/common.sh:_find_claude_pid. The embedded
NOTIFY_SCRIPT in clawd_tank_menubar/hooks.py duplicates this logic — keep them
in sync.
"""

import os
import re
import subprocess

_CLAUDE_ARGV_RE = re.compile(r"(^|/)claude($|\s)")


def _ps(field: str, pid: int) -> str:
    """Run `ps -o <field>= -p <pid>` and return trimmed stdout. Empty on error."""
    try:
        r = subprocess.run(
            ["ps", "-o", f"{field}=", "-p", str(pid)],
            capture_output=True, text=True, timeout=1.0,
        )
        return r.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return ""


def find_claude_pid() -> int:
    """Walk from os.getppid() up the process tree to find the long-lived
    Claude Code PID. Falls back to os.getppid() if no `claude` ancestor found.

    Identification (in order, per ancestor):
      1. `ps -o comm=` exactly equals "claude" (native binary)
      2. `ps -o command=` matches (^|/)claude($|\\s) (node-wrapped install)

    Loose substring matching against argv is DELIBERATELY AVOIDED — see the
    agent-bus comment block (lib/common.sh:36-47) for the rationale.
    """
    start = os.getppid()
    pid = start
    while pid > 1:
        if _ps("comm", pid) == "claude":
            return pid
        if _CLAUDE_ARGV_RE.search(_ps("command", pid)):
            return pid
        ppid_str = _ps("ppid", pid)
        try:
            pid = int(ppid_str)
        except ValueError:
            break
    return start
