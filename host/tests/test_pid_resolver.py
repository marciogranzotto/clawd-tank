"""Tests for find_claude_pid — walks process ancestors via `ps` to find the
long-lived Claude Code PID, mirroring claude-plugins/agent-bus/lib/common.sh.
"""

from unittest.mock import patch, MagicMock

from clawd_tank_daemon.pid_resolver import find_claude_pid


def _make_ps_mock(responses):
    """responses: dict of (field, pid) -> raw stdout string (will get a trailing newline).

    Each subprocess.run call gets args=["ps", "-o", "<field>=", "-p", "<pid>"].
    Returns MagicMock with .stdout matching the responses table, empty otherwise.
    """
    def side_effect(args, **kwargs):
        field = args[2].rstrip("=")
        pid = int(args[4])
        out = responses.get((field, pid), "")
        return MagicMock(stdout=out + "\n", returncode=0)
    return side_effect


def test_direct_parent_is_claude():
    """getppid() returns claude — return it directly."""
    with patch("clawd_tank_daemon.pid_resolver.os.getppid", return_value=100):
        with patch("clawd_tank_daemon.pid_resolver.subprocess.run") as run:
            run.side_effect = _make_ps_mock({
                ("comm", 100): "claude",
            })
            assert find_claude_pid() == 100


def test_walks_past_shell_wrapper():
    """getppid() is zsh, ancestor is claude — walk and find it."""
    with patch("clawd_tank_daemon.pid_resolver.os.getppid", return_value=200):
        with patch("clawd_tank_daemon.pid_resolver.subprocess.run") as run:
            run.side_effect = _make_ps_mock({
                ("comm", 200): "zsh",
                ("command", 200): "zsh -c /Users/me/.clawd-tank/clawd-tank-notify",
                ("ppid", 200): "300",
                ("comm", 300): "claude",
            })
            assert find_claude_pid() == 300


def test_node_install_matches_via_command_regex():
    """Node-wrapped install: comm is 'node', argv invokes a path ending in `claude`."""
    with patch("clawd_tank_daemon.pid_resolver.os.getppid", return_value=400):
        with patch("clawd_tank_daemon.pid_resolver.subprocess.run") as run:
            run.side_effect = _make_ps_mock({
                ("comm", 400): "node",
                ("command", 400): "node /opt/claude-code/bin/claude --resume xyz",
            })
            assert find_claude_pid() == 400


def test_node_install_with_path_prefix_matches():
    """Command starts with /usr/local/bin/claude."""
    with patch("clawd_tank_daemon.pid_resolver.os.getppid", return_value=500):
        with patch("clawd_tank_daemon.pid_resolver.subprocess.run") as run:
            run.side_effect = _make_ps_mock({
                ("comm", 500): "claude-wrapper",
                ("command", 500): "/usr/local/bin/claude --resume abc",
            })
            assert find_claude_pid() == 500


def test_shell_snapshot_does_NOT_match():
    """Critical negative case (agent-bus calls this out): the Bash tool spawns
    `zsh -c '... ~/.claude/shell-snapshots/...'`. A loose substring would match
    the zsh's argv. The strict `comm == "claude"` and `(^|/)claude($|\\s)` regex
    must NOT match this shell.
    """
    with patch("clawd_tank_daemon.pid_resolver.os.getppid", return_value=600):
        with patch("clawd_tank_daemon.pid_resolver.subprocess.run") as run:
            run.side_effect = _make_ps_mock({
                ("comm", 600): "zsh",
                ("command", 600): "zsh -c source ~/.claude/shell-snapshots/snap-12345.sh; /tmp/cmd",
                ("ppid", 600): "1",  # walk terminates
            })
            # No claude ancestor found, falls back to getppid()
            assert find_claude_pid() == 600


def test_falls_back_to_getppid_when_no_claude_ancestor():
    """Walk reaches pid 1 without finding claude — return original getppid()."""
    with patch("clawd_tank_daemon.pid_resolver.os.getppid", return_value=700):
        with patch("clawd_tank_daemon.pid_resolver.subprocess.run") as run:
            run.side_effect = _make_ps_mock({
                ("comm", 700): "zsh",
                ("command", 700): "zsh",
                ("ppid", 700): "1",
            })
            assert find_claude_pid() == 700


def test_falls_back_to_getppid_when_ps_fails():
    """ps returns empty (process gone mid-walk) — fall back gracefully."""
    with patch("clawd_tank_daemon.pid_resolver.os.getppid", return_value=800):
        with patch("clawd_tank_daemon.pid_resolver.subprocess.run") as run:
            run.side_effect = _make_ps_mock({})  # all empty
            assert find_claude_pid() == 800


def test_ps_timeout_does_not_raise():
    """subprocess timeout returns empty string, walk falls back."""
    import subprocess as sp
    with patch("clawd_tank_daemon.pid_resolver.os.getppid", return_value=900):
        with patch("clawd_tank_daemon.pid_resolver.subprocess.run") as run:
            run.side_effect = sp.TimeoutExpired(cmd="ps", timeout=1.0)
            assert find_claude_pid() == 900
