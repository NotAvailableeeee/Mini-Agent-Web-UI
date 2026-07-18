"""Workspace file browser — tree + file content endpoints.

Exposes the agent's workspace directory as a read-only file tree so the
web UI can let users preview files the agent has produced (HTML reports,
CSV charts, generated Markdown, etc.).

Security:
- All incoming paths are resolved against the workspace dir and rejected
  if they escape it (defends against `?path=../../etc/passwd`).
- File reads are size-capped (2 MB) to avoid OOM on huge files; the
  response flags `truncated: true` so the UI can warn the user.

Filtering:
- Hidden entries (`.foo`), `__pycache__`, `.git`, `node_modules`, etc.
  are skipped to keep the tree readable.
- Tree depth is capped (6) so a runaway `node_modules` symlink can't
  pin the request.
"""

from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..config import load_web_config


router = APIRouter()


# Directories we always skip — these accumulate as build/test noise and
# would dominate the tree without telling the user anything useful.
SKIP_DIRS = {"__pycache__", ".git", ".venv", "node_modules", ".cache", ".mypy_cache"}
SKIP_FILES = {".DS_Store", "Thumbs.db"}
MAX_DEPTH = 6
MAX_FILE_SIZE = 2 * 1024 * 1024  # 2 MB hard cap on file read
# Extensions we treat as text even when mimetypes doesn't know them
# (e.g. `.py`, `.vue`). Everything else falls through to binary detection.
TEXT_EXTENSIONS = {
    ".txt", ".md", ".rst", ".tsv",
    ".py", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs",
    ".css", ".scss", ".less",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env",
    ".xml", ".svg", ".html", ".htm", ".vue", ".svelte",
    ".sh", ".bash", ".zsh", ".fish",
    ".sql", ".log",
    ".rs", ".go", ".java", ".kt", ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp",
    ".rb", ".php", ".pl", ".lua", ".r", ".dart",
}

# Extension → syntax-highlight language hint. Used when kind == "code"
# so the frontend can pick a highlighter. Mirrors the languages
# `highlight.js` ships by default; if/when a highlighter is wired in
# these strings will be the keys it looks up.
CODE_LANGUAGES = {
    ".py": "python", ".pyx": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript",
    ".jsx": "jsx", ".tsx": "tsx",
    ".vue": "vue", ".svelte": "svelte",
    ".css": "css", ".scss": "scss", ".less": "less",
    ".yaml": "yaml", ".yml": "yaml",
    ".xml": "xml", ".svg": "xml",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash", ".fish": "bash",
    ".sql": "sql",
    ".rs": "rust",
    ".go": "go",
    ".java": "java", ".kt": "kotlin",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".lua": "lua",
    ".r": "r",
    ".dart": "dart",
}


def _safe_resolve(workspace: Path, raw_path: str) -> Path:
    """Resolve `raw_path` against workspace; raise 400/403 if it escapes.

    `raw_path` is treated as relative to the workspace root. Absolute
    paths and NUL bytes are rejected up front; symlinks are resolved
    before the containment check so a `workspace/link -> /etc` attack
    can't bypass the check.
    """
    if not raw_path or "\x00" in raw_path:
        raise HTTPException(status_code=400, detail="invalid path")
    workspace_resolved = workspace.resolve()
    candidate = (workspace / raw_path).resolve()
    try:
        candidate.relative_to(workspace_resolved)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="path escapes workspace") from exc
    return candidate


def _build_tree(directory: Path, workspace: Path, depth: int) -> list[dict[str, Any]]:
    """Recursively list `directory` (rooted at `workspace`).

    Directories come first, then files, both alphabetised. Returns
    `[]` if `depth` exceeds MAX_DEPTH so the UI doesn't try to render
    a runaway subtree.
    """
    if depth >= MAX_DEPTH:
        return []
    entries: list[dict[str, Any]] = []
    try:
        # Sort: directories first, then by name (case-insensitive).
        children = sorted(
            directory.iterdir(),
            key=lambda p: (not p.is_dir(), p.name.lower()),
        )
    except (PermissionError, FileNotFoundError):
        return entries
    for child in children:
        name = child.name
        if name in SKIP_DIRS or name in SKIP_FILES:
            continue
        # Skip dotfiles except a small set the user probably cares about.
        if name.startswith(".") and name not in {".gitignore", ".env", ".env.example"}:
            continue
        try:
            stat = child.stat()
        except OSError:
            continue
        rel = child.relative_to(workspace).as_posix()
        if child.is_dir():
            entries.append({
                "name": name,
                "path": rel,
                "type": "directory",
                "modified": stat.st_mtime,
                "children": _build_tree(child, workspace, depth + 1),
            })
        elif child.is_file():
            entries.append({
                "name": name,
                "path": rel,
                "type": "file",
                "size": stat.st_size,
                "modified": stat.st_mtime,
            })
    return entries


@router.get("/tree")
async def workspace_tree() -> dict[str, Any]:
    """Return the workspace tree, depth-first, sorted."""
    cfg = load_web_config()
    workspace = cfg.workspace_dir
    if not workspace.exists():
        return {"workspace": str(workspace), "entries": []}
    return {
        "workspace": str(workspace),
        "entries": _build_tree(workspace, workspace, depth=0),
    }


class FileContent(BaseModel):
    path: str
    name: str
    size: int
    mime: str
    # One of: "text" | "markdown" | "json" | "jsonl" | "csv" | "code" | "html"
    #       | "image" | "pdf" | "binary"
    # Tells the UI how to render `content`.
    kind: str
    # For text/markdown/json/jsonl/code/html: UTF-8 string.
    # For image/pdf/binary: base64.
    content: str
    # When kind == "code", the language hint from CODE_LANGUAGES
    # (e.g. "python", "javascript"). The UI can use it to pick a
    # syntax highlighter or just to display "PYTHON" in the badge.
    language: str | None = None
    # When kind == "jsonl": one entry per non-blank line, parsed
    # individually so a malformed line doesn't kill the whole read.
    #   {"line": int, "ok": bool,
    #    "type": str,        # the record's `type` field if dict-shaped
    #    "data": Any,        # parsed JSON object/array
    #    "raw": str,         # raw line, for display on parse error
    #    "error": str}       # populated only when ok=False
    # UI uses `data` for the formatted view and `error` to flag
    # bad lines visually (so users can spot a typo'd entity/relation
    # that the graph silently ignored).
    records: list[dict] | None = None
    truncated: bool = False
    truncated_size: int | None = None  # bytes actually read when truncated


@router.get("/file", response_model=FileContent)
async def workspace_file(path: str = Query(..., description="Path relative to workspace")) -> FileContent:
    """Return the content of a single workspace file.

    - `kind` is computed from mimetype + extension; the UI uses it to pick
      between `<pre>`, sandboxed `<iframe>`, `<img>`, PDF viewer, etc.
    - Files larger than 2 MB are read up to that cap and flagged
      `truncated=true` so the UI can warn the user.
    - Binary files return base64 in `content`; small text files (UTF-8)
      return the raw string. Files with mixed/bad encoding fall back to
      text-with-replacement so the user at least sees something.
    """
    cfg = load_web_config()
    workspace = cfg.workspace_dir
    target = _safe_resolve(workspace, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")

    stat = target.stat()
    truncated = False
    if stat.st_size > MAX_FILE_SIZE:
        with open(target, "rb") as f:
            data = f.read(MAX_FILE_SIZE)
        truncated = True
    else:
        with open(target, "rb") as f:
            data = f.read()

    mime, _ = mimetypes.guess_type(str(target))
    mime = mime or "application/octet-stream"
    ext = target.suffix.lower()
    language: str | None = None  # populated for code/markdown/json kinds
    records: list[dict] | None = None  # populated only for kind=="jsonl"

    # Classify the file. Order matters: image and pdf are subsets of
    # "binary" but get their own kinds so the UI can render them inline.
    if mime.startswith("image/"):
        kind = "image"
        content = base64.b64encode(data).decode("ascii")
        language = None
    elif mime == "application/pdf" or ext == ".pdf":
        kind = "pdf"
        content = base64.b64encode(data).decode("ascii")
        language = None
    elif mime == "text/html" or ext in (".html", ".htm"):
        kind = "html"
        content = data.decode("utf-8", errors="replace")
        language = None
    elif mime == "text/markdown" or ext in (".md", ".markdown"):
        # Rendered as markdown in the UI — distinguishing from
        # generic text avoids the user staring at raw `#` / `-` in
        # their README.
        kind = "markdown"
        content = data.decode("utf-8", errors="replace")
        language = "markdown"
    elif mime == "application/json" or ext == ".json":
        # Pretty-print JSON in the UI so nested objects/arrays are
        # readable; the user can still scroll to see everything.
        #
        # NOTE: the `.mcp_memory.jsonl` special-case is NOT here —
        # that file's extension is `.jsonl` (not `.json`) and Python's
        # mimetypes module doesn't recognise it, so we lock on
        # `target.name` in its own branch higher up. Keeping the
        # special-case in this branch would silently fall through to
        # the generic text fallback (per-row JSONL parsing never
        # runs). See the match right after the markdown branch.
        kind = "json"
        content = data.decode("utf-8", errors="replace")
        language = "json"
    elif target.name == ".mcp_memory.jsonl":
        # JSONL is *not* a single JSON document — it's one record
        # per line. Returning kind="json" would make the frontend
        # call JSON.parse() on the whole file and fail. Returning
        # kind="text" makes the user stare at a wall of minified
        # JSON with no per-record structure. Instead emit kind="jsonl"
        # with `records` parsed line-by-line so a single bad line
        # doesn't take the whole file down, AND the UI can pretty-
        # print each record on its own row.
        #
        # Placed BEFORE the catch-all (and outside the JSON mime/ext
        # branch) because `.jsonl` isn't a known mime type or
        # extension to Python's mimetypes — that's exactly why the
        # file used to silently fall through to plain text and why
        # this branch is keyed on the literal filename.
        kind = "jsonl"
        text = data.decode("utf-8", errors="replace")
        content = text
        language = None
        records: list[dict] = []
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
                rec_type = ""
                if isinstance(obj, dict):
                    t = obj.get("type")
                    if isinstance(t, str):
                        rec_type = t
                records.append({
                    "line": i,
                    "ok": True,
                    "type": rec_type,
                    "data": obj,
                    "raw": stripped,
                })
            except json.JSONDecodeError as e:
                # Don't drop the line — surface it so the user
                # can see WHERE in the file the bad record is.
                records.append({
                    "line": i,
                    "ok": False,
                    "type": "",
                    "data": None,
                    "raw": stripped,
                    "error": f"{e.msg} (line {e.lineno}, col {e.colno})",
                })
    elif mime == "text/csv" or ext in (".csv",):
        # CSV — kept as raw text (no parsing on the backend) so the
        # frontend can render it as a real table. Frontend's parser
        # is small and handles quoted fields.
        kind = "csv"
        content = data.decode("utf-8", errors="replace")
        language = None
    elif ext in CODE_LANGUAGES:
        # Code with a known language — the UI can pick a highlighter
        # (e.g. "python") and show "PYTHON" in the badge. Even
        # without highlighting, marking the block as "code" tells
        # the user this isn't prose.
        kind = "code"
        content = data.decode("utf-8", errors="replace")
        language = CODE_LANGUAGES[ext]
    elif mime.startswith("text/") or ext in TEXT_EXTENSIONS:
        # Plain text (txt, log, csv, etc.) — render as monospace pre,
        # no special treatment.
        kind = "text"
        content = data.decode("utf-8", errors="replace")
        language = None
    else:
        # Heuristic for unknown types: if it decodes cleanly as UTF-8 and
        # has no control bytes, treat as text; otherwise base64.
        try:
            text = data.decode("utf-8")
            has_control = any(
                (ord(c) < 0x20 and c not in "\t\n\r") or ord(c) == 0x7f
                for c in text[:4096]
            )
            if has_control:
                kind = "binary"
                content = base64.b64encode(data).decode("ascii")
            else:
                kind = "text"
                content = text
            language = None
        except UnicodeDecodeError:
            kind = "binary"
            content = base64.b64encode(data).decode("ascii")
            language = None

    return FileContent(
        path=path,
        name=target.name,
        size=stat.st_size,
        mime=mime,
        kind=kind,
        content=content,
        language=language,
        records=records if kind == "jsonl" else None,
        truncated=truncated,
        truncated_size=MAX_FILE_SIZE if truncated else None,
    )
