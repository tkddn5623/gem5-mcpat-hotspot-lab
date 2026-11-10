"""Tool discovery shared by local setup, checks and experiment runs."""
import json
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
TOOL_FILE = ROOT / ".local-tools.json"


def resolve_tools(args):
    saved = json.loads(TOOL_FILE.read_text()) if TOOL_FILE.is_file() else {}
    for name, executable, docker in (
        ("gem5", "gem5.opt", "/opt/gem5/build/X86/gem5.opt"),
        ("mcpat", "mcpat", "/opt/mcpat/mcpat"),
        ("hotspot", "hotspot", "/opt/HotSpot/hotspot"),
    ):
        requested = getattr(args, name)
        path = requested or os.environ.get(name.upper() + "_BINARY") or saved.get(name)
        path = path or shutil.which(executable) or docker
        setattr(args, name, str(Path(path).expanduser()) if "/" in path else path)
