"""PostToolUse hook: format and lint the edited .py file with ruff.

Claude Code passes the tool call as JSON on stdin (tool_input.file_path).
"""

import json
import subprocess
import sys

path = json.load(sys.stdin).get("tool_input", {}).get("file_path", "")
if path.endswith(".py"):
    subprocess.run([sys.executable, "-m", "ruff", "format", "--quiet", path])
    subprocess.run([sys.executable, "-m", "ruff", "check", "--fix", "--quiet", path])
