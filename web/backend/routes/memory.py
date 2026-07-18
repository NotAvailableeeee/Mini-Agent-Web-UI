"""Memory endpoints — short-term (agent.messages), long-term notes
(workspace/.agent_memory.json), and long-term graph (workspace/.mcp_memory.jsonl).

All three are read-only. The frontend polls these after each step_end
so the panel reflects the current agent state without blocking the
chat loop.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from ..agent_runner import get_runner
from ..config import load_web_config


router = APIRouter()

# Path of the knowledge-graph JSON inside workspace/. Matches the value
# configured in `mini_agent/config/mcp.json` via the MEMORY_FILE_PATH
# env var.
_GRAPH_FILE = ".mcp_memory.jsonl"


def _flatten_content(content) -> str:
    """`Message.content` is either a string or a list of content blocks
    (e.g. tool result blocks). Flatten to a single string for the
    memory panel's preview; the full content is still in `content`.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)
    parts = []
    for block in content:
        if isinstance(block, dict):
            t = block.get("type")
            if t == "text":
                parts.append(block.get("text", ""))
            elif t == "tool_use":
                # `name` and a short id are enough for the panel
                parts.append(f"[tool: {block.get('name', '?')}]")
            elif t == "tool_result":
                # tool result content can be a string or a list
                inner = block.get("content")
                if isinstance(inner, str):
                    parts.append(inner)
                elif isinstance(inner, list):
                    for sub in inner:
                        if isinstance(sub, dict) and sub.get("type") == "text":
                            parts.append(sub.get("text", ""))
        else:
            parts.append(str(block))
    return "\n".join(parts)


@router.get("/short")
async def short_term_memory() -> dict[str, Any]:
    """Agent's in-process messages — the working context the LLM
    sees on the next call.

    The system prompt (always at index 0) is excluded; everything
    after it is "short-term memory" in the user's mental model.
    """
    runner = get_runner()
    raw = await runner.get_messages()

    messages: list[dict[str, Any]] = []
    for i, msg in enumerate(raw):
        if i == 0 and msg.role == "system":
            continue
        entry: dict[str, Any] = {
            "role": msg.role,
            "content": _flatten_content(msg.content),
        }
        if msg.thinking:
            entry["thinking"] = msg.thinking
        if msg.name:
            # For tool messages, this is the tool name (e.g. "bash").
            entry["name"] = msg.name
        if msg.tool_calls:
            # Per-tool-call detail: name + the arguments the LLM emitted.
            # Frontend renders the name as a chip and the arguments
            # underneath as a small monospace preview so the user can see
            # at a glance *what* the agent asked the tool to do (queries,
            # urls, file paths, etc.) without expanding the full card.
            entry["tool_calls"] = [
                {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments or {},
                }
                for tc in msg.tool_calls if tc.function
            ]
        messages.append(entry)

    return {"messages": messages, "count": len(messages)}


@router.get("/long")
async def long_term_memory() -> dict[str, Any]:
    """Notes from ``workspace/.agent_memory.json`` — the long-term
    memory written by the agent's ``record_note`` tool.

    Returns ``{"notes": [], "exists": false}`` if the file is
    missing (the file is lazy-created on first record_note call).
    """
    cfg = load_web_config()
    memory_file = cfg.workspace_dir / ".agent_memory.json"
    if not memory_file.exists():
        return {"notes": [], "exists": False, "count": 0}
    try:
        notes = json.loads(memory_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"notes": [], "exists": True, "error": "parse failed", "count": 0}
    if not isinstance(notes, list):
        return {"notes": [], "exists": True, "error": "malformed (not a list)", "count": 0}
    return {"notes": notes, "exists": True, "count": len(notes)}


@router.get("/graph")
async def long_term_graph() -> dict[str, Any]:
    """Knowledge graph from ``workspace/.mcp_memory.jsonl`` —
    a JSONL file (one record per line) where each line is either
    an entity (``{"type": "entity", ...}``) or a relation
    (``{"type": "relation", ...}``). Records are partitioned by the
    ``type`` field and reassembled into the canonical
    ``{entities, relations}`` shape the frontend expects.

    Returns ``{"entities": [], "relations": [], "exists": false}``
    when the file does not exist yet (lazy-created on first
    `create_entities` call). The frontend treats "missing" the
    same as "empty" so the panel renders a friendly placeholder
    instead of a 404.
    """
    cfg = load_web_config()
    graph_file = cfg.workspace_dir / _GRAPH_FILE
    if not graph_file.exists():
        return {
            "entities": [],
            "relations": [],
            "exists": False,
            "entity_count": 0,
            "relation_count": 0,
        }
    entities: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    try:
        raw = graph_file.read_text(encoding="utf-8")
    except OSError:
        return {
            "entities": [],
            "relations": [],
            "exists": True,
            "error": "read failed",
            "entity_count": 0,
            "relation_count": 0,
        }
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            # A malformed line shouldn't kill the whole read —
            # skip it and let the rest of the graph render.
            continue
        if not isinstance(rec, dict):
            continue
        kind = rec.get("type")
        if kind == "entity":
            entities.append(rec)
        elif kind == "relation":
            relations.append(rec)
        # Unknown record types (missing/typo'd `type` field) are
        # silently ignored — they're hand-edited junk, not data.
    return {
        "entities": entities,
        "relations": relations,
        "exists": True,
        "entity_count": len(entities),
        "relation_count": len(relations),
    }
