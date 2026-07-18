"""AgentRunner — thin wrapper around ``mini_agent.agent.Agent``.

M1 scope: synchronous request/response only. No streaming, no cancellation,
no multi-session persistence (those are M2+).

The runner is a process-wide singleton, lazily initialised on first use.
It builds the same Agent instance as the CLI/ACP front-ends by reusing
``initialize_base_tools`` and ``add_workspace_tools`` from ``mini_agent.cli``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from time import perf_counter
from typing import Any, Optional

from mini_agent.agent import Agent
from mini_agent.cli import add_workspace_tools, initialize_base_tools
from mini_agent.config import Config
from mini_agent.llm import LLMClient
from mini_agent.retry import RetryConfig
from mini_agent.schema import LLMProvider, LLMResponse, Message, TokenUsage
from mini_agent.tools.note_tool import RecallNoteTool

from .config import WebConfig, load_web_config


# Async callback invoked once per emitted event.
EventCallback = Callable[[dict], Awaitable[None]]


# Repo root used to render paths as "Mini-Agent/..." (relative to the
# repo, not the user's home directory). Cached at module load.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _relpath(p: Optional[Path]) -> Optional[str]:
    """Render a path as ``Mini-Agent/<repo-relative>`` for display.

    Files outside the repo (rare — e.g. a workspace set to ``~/foo``)
    fall back to their absolute form. ``None`` passes through.
    """
    if p is None:
        return None
    p = Path(p)
    try:
        rel = p.resolve().relative_to(_REPO_ROOT)
        return f"Mini-Agent/{rel}"
    except (ValueError, OSError):
        return str(p)


# Cache the agent_runner.py source on first read. ``_read_self_section``
# uses this to extract the actual code that ran for each init step, so
# the inspector shows the *real* lines rather than static snippets.
_SELF_SOURCE_CACHE: Optional[str] = None


def _read_self_source() -> str:
    """Return this file's source (cached)."""
    global _SELF_SOURCE_CACHE
    if _SELF_SOURCE_CACHE is None:
        try:
            _SELF_SOURCE_CACHE = Path(__file__).read_text(encoding="utf-8")
        except OSError:
            _SELF_SOURCE_CACHE = ""
    return _SELF_SOURCE_CACHE


def _read_self_section(marker: str) -> tuple[str, int]:
    """Return ``(content, start_line)`` for the source between
    ``# region: <marker>`` and the matching ``# endregion: <marker>``.

    ``start_line`` is the 1-based line number of the **first line of
    content** (i.e. the line right after the ``# region: <marker>``
    line). Lines are dedented so they read cleanly in the inspector.

    Returns ``("", 0)`` if the marker isn't found.
    """
    import re
    from textwrap import dedent

    content = _read_self_source()
    # Allow optional whitespace after the marker in the close tag so
    # `# endregion: init-step-1` and `#  endregion: init-step-1` both match.
    pattern = rf"#\s*region:\s*{re.escape(marker)}\s*\n(.*?)#\s*endregion:\s*{re.escape(marker)}"
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        return ("", 0)
    # Line number of the ``# region: <marker>`` line itself.
    region_line = content[: match.start()].count("\n") + 1
    # The first line of actual code is the line right after.
    content_start_line = region_line + 1
    return (dedent(match.group(1)).rstrip(), content_start_line)


def _enrich_event(ev: dict[str, Any]) -> dict[str, Any]:
    """Add ``source_code`` / line range to an init event if it has a marker.

    Used by both ``init_trace()`` (final done event) and the streaming
    SSE path (``step_complete`` events) so the frontend receives the
    same payload shape regardless of delivery channel. Returns a shallow
    copy so callers can mutate the in-memory event without affecting
    later enrichments.
    """
    out = dict(ev)
    call = out.get("call")
    if call and call.get("source_marker"):
        marker = call["source_marker"]
        src, start_line = _read_self_section(marker)
        if src:
            call["source_code"] = src
            call["source_line_start"] = start_line
            # end line = start + 行数 - 1 (闭区间)
            call["source_line_end"] = start_line + src.count("\n")
            call["source_file"] = "Mini-Agent/web/backend/agent_runner.py"
        call.pop("source_marker", None)
    return out


# Tool family classification. Used by ``init_trace()`` to group tools
# into the 4 visible buckets (Base tools / Skills / MCP / Workspace
# tools). Class-based lookup since MCP tools have arbitrary names —
# anything not in the known classes falls through to "mcp".
_BASE_TOOL_CLASSES = {"BashOutputTool", "BashKillTool"}
_SKILL_TOOL_CLASSES = {"GetSkillTool"}
_WORKSPACE_TOOL_CLASSES = {
    "BashTool",
    "ReadTool",
    "WriteTool",
    "EditTool",
    "SessionNoteTool",
    "RecallNoteTool",
}


def _classify_tool(tool: Any, default: str = "mcp") -> str:
    """Return the family label for a tool instance.

    Args:
        tool: A Tool instance from ``initialize_base_tools`` or
            ``add_workspace_tools``.
        default: Family returned when the tool's class isn't in any
            known set. Defaults to ``"mcp"`` since unknown tools in
            this build are loaded from MCP servers.

    Returns:
        One of ``"base"``, ``"skill"``, ``"workspace"``, or ``default``.
    """
    cls_name = type(tool).__name__
    if cls_name in _BASE_TOOL_CLASSES:
        return "base"
    if cls_name in _SKILL_TOOL_CLASSES:
        return "skill"
    if cls_name in _WORKSPACE_TOOL_CLASSES:
        return "workspace"
    return default


def _summarize_config(cfg: Any) -> dict[str, Any]:
    """Flatten the top-level Config into a dict the inspector can render.

    Skips secrets (api_key is reduced to a boolean).
    """
    return {
        "llm.provider": cfg.llm.provider,
        "llm.model": cfg.llm.model,
        "llm.api_base": cfg.llm.api_base,
        "llm.api_key_set": bool(cfg.llm.api_key) and cfg.llm.api_key != "YOUR_API_KEY_HERE",
        "llm.retry.enabled": cfg.llm.retry.enabled,
        "llm.retry.max_retries": cfg.llm.retry.max_retries,
        "llm.retry.initial_delay": cfg.llm.retry.initial_delay,
        "agent.max_steps": cfg.agent.max_steps,
        "agent.system_prompt_path": cfg.agent.system_prompt_path,
        "tools.enable_bash": cfg.tools.enable_bash,
        "tools.enable_skills": cfg.tools.enable_skills,
        "tools.enable_mcp": cfg.tools.enable_mcp,
        "tools.enable_file_tools": cfg.tools.enable_file_tools,
        "tools.enable_note": cfg.tools.enable_note,
        "tools.skills_dir": cfg.tools.skills_dir,
        "tools.mcp_config_path": cfg.tools.mcp_config_path,
    }


def _build_tool_families_summary(tools: list, families: dict[str, str]) -> list[dict[str, Any]]:
    """Bucket tools into the 4 visible families with tool names + descriptions."""
    bucket: dict[str, list[dict[str, Any]]] = {
        "Base tools": [], "Skills": [], "MCP": [], "Workspace tools": [],
    }
    family_for_kind = {
        "base": "Base tools", "skill": "Skills",
        "workspace": "Workspace tools", "mcp": "MCP",
    }
    for t in tools:
        kind = families.get(t.name, "mcp")
        bucket[family_for_kind[kind]].append({"name": t.name, "kind": kind})
    return [{"name": fam, "items": items} for fam, items in bucket.items() if items]


# ---------------------------------------------------------------------------
# LLM I/O inspector helpers — local Anthropic wire-format converters.
#
# Why local copies (instead of reusing ``AnthropicClient._convert_messages``)?
#   1. Memory constraint: we may only edit files under web/. ``anthropic_client.py``
#      lives outside that scope, so we can't add hooks there.
#   2. We need the wire format BEFORE the actual LLM call returns (so the SSE
#      ``llm_request`` event can show what was sent). The private ``_convert_messages``
#      method is an implementation detail we shouldn't depend on from outside.
#
# The conversion logic is intentionally identical to
# ``mini_agent/llm/anthropic_client.py:_convert_messages`` — see that file's
# docstring for the format details. If Anthropic adds a new block type, both
# copies must be updated in lockstep.
# ---------------------------------------------------------------------------


def _anthropic_convert_messages_for_inspect(
    messages: list[Message],
) -> tuple[Optional[str], list[dict[str, Any]]]:
    """Convert internal ``Message`` list to Anthropic wire format.

    Mirrors ``AnthropicClient._convert_messages`` (anthropic_client.py:114-178).
    Returns ``(system_message, api_messages)`` so the caller can build the full
    ``messages.create`` payload (Anthropic takes system as a top-level param,
    not inside messages).
    """
    system_message: Optional[str] = None
    api_messages: list[dict[str, Any]] = []

    for msg in messages:
        if msg.role == "system":
            system_message = msg.content if isinstance(msg.content, str) else str(msg.content)
            continue

        # For user and assistant messages
        if msg.role in ("user", "assistant"):
            # Assistant messages with thinking or tool calls become content blocks
            if msg.role == "assistant" and (msg.thinking or msg.tool_calls):
                content_blocks: list[dict[str, Any]] = []

                # Add thinking block if present
                if msg.thinking:
                    content_blocks.append({"type": "thinking", "thinking": msg.thinking})

                # Add text content if present
                if msg.content:
                    content_blocks.append({"type": "text", "text": msg.content})

                # Add tool use blocks
                if msg.tool_calls:
                    for tool_call in msg.tool_calls:
                        content_blocks.append(
                            {
                                "type": "tool_use",
                                "id": tool_call.id,
                                "name": tool_call.function.name,
                                "input": tool_call.function.arguments,
                            }
                        )

                api_messages.append({"role": "assistant", "content": content_blocks})
            else:
                api_messages.append({"role": msg.role, "content": msg.content})

        # For tool result messages — Anthropic uses user role with tool_result blocks
        elif msg.role == "tool":
            api_messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.tool_call_id,
                            "content": msg.content,
                        }
                    ],
                }
            )

    return system_message, api_messages


def _anthropic_convert_tools_for_inspect(tools) -> list[dict[str, Any]]:
    """Convert Tool list to Anthropic tool schema.

    Reuses ``Tool.to_schema()`` (mini_agent/tools/base.py:38, public method) —
    no need to duplicate the schema shape here.
    """
    return [t.to_schema() for t in tools]


def _serialize_llm_response(response: LLMResponse) -> dict[str, Any]:
    """Serialize an LLMResponse for the frontend ``llm_response`` SSE event.

    Anthropic-specific: splits out ``cache_read_input_tokens`` and
    ``cache_creation_input_tokens`` from the collapsed ``prompt_tokens``
    so the UI can display them as sub-rows.

    Cache fields default to 0 when absent (OpenAI path or older API
    responses that don't include them) so the frontend can render a
    consistent 4-row usage block.
    """
    usage_dict: Optional[dict[str, int]] = None
    if response.usage is not None:
        # The Anthropic client already folds cache_read + cache_creation into
        # prompt_tokens (anthropic_client.py:241-242). We don't have the
        # original cache numbers here, so the frontend can only show them
        # if AnthropicClient is patched to expose them. For now, surface
        # the three core numbers and leave cache sub-rows hidden.
        usage_dict = {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
            # Cache breakdown — populated when anthropic_client.py starts
            # exposing them; default 0 keeps the UI stable.
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }

    return {
        "content": response.content,
        "thinking": response.thinking,
        "tool_calls": (
            [
                {
                    "id": tc.id,
                    "type": tc.type,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in response.tool_calls
            ]
            if response.tool_calls
            else None
        ),
        "finish_reason": response.finish_reason,
        "usage": usage_dict,
    }


class AgentRunner:
    """Manages one ``Agent`` instance for the lifetime of the web server."""

    def __init__(self, config: WebConfig) -> None:
        self._config = config
        self._lock = asyncio.Lock()
        self._agent: Optional[Agent] = None
        self._system_prompt: str = ""
        self._tool_names: list[str] = []
        # Tracks how many ``agent.run()`` calls are currently in flight. Used
        # by ``clear()`` to refuse mid-run (we don't try to cancel — that
        # would require plumbing a cancellation token into the SSE route and
        # is out of scope for the M1 /clear feature).
        self._in_flight: int = 0
        # Cached references used by ``init_trace()``. Populated during the
        # first ``_ensure_agent()`` call. Kept on the runner so the trace
        # endpoint doesn't have to redo skills discovery or guess which
        # family a tool came from.
        self._skill_loader: Optional[Any] = None
        # Per-tool family classification ("base" | "skill" | "mcp" |
        # "workspace"). Built once during init from tool class + name.
        self._tool_families: dict[str, str] = {}

    async def _ensure_agent(
        self,
        on_step_start: Optional[EventCallback] = None,
        on_step_complete: Optional[EventCallback] = None,
    ) -> Agent:
        """Lazily build the Agent on first request.

        Also captures an ``_init_events`` log — each step records the
        actual function called, the real parameter values passed, and
        a summary of what was returned. The AgentInitInspector frontend
        reads this to show *what actually happened* during init, not a
        static walkthrough of the code.

        ``on_step_start`` / ``on_step_complete`` are optional async
        callbacks invoked once per step (just before the step runs and
        right after its event is captured). The SSE route uses them to
        stream progress to the frontend so the inspector can animate
        each step as it completes, instead of waiting for the full
        trace. Both default to ``None``, so existing callers that don't
        need streaming (the chat run_loop, etc.) keep working unchanged.

        **Replay semantics**: when the agent is already built (e.g. the
        chat ``/api/info`` endpoint triggered init earlier without any
        callbacks), we still re-fire the cached events through any
        callbacks the caller passed — otherwise the SSE stream would
        jump straight to ``done`` and the inspector would only ever see
        "all 5 steps done at once" with no per-step progress.
        """
        if self._agent is not None:
            # Agent already built (likely by an earlier caller like
            # /api/info that didn't pass callbacks). Replay the cached
            # events so SSE consumers still see per-step progression.
            if on_step_start or on_step_complete:
                await self._replay_init_events(on_step_start, on_step_complete)
            return self._agent

        async with self._lock:
            if self._agent is not None:  # double-check after acquiring lock
                # Same replay for the second arriver inside the lock.
                if on_step_start or on_step_complete:
                    await self._replay_init_events(on_step_start, on_step_complete)
                return self._agent

            self._config.workspace_dir.mkdir(parents=True, exist_ok=True)

            # Container that init_trace() will read later. Reset on
            # every init so stale data from a previous run can't leak.
            self._init_events: list[dict[str, Any]] = []

            # ── Step 1: Load config ────────────────────────────────────
            if on_step_start:
                await on_step_start({"n": 1, "title": "Load config"})
            # region: init-step-1
            config_path = Config.get_default_config_path()
            if not config_path.exists():
                raise FileNotFoundError(
                    f"Configuration file not found: {config_path}. "
                    "Run the setup script or copy config-example.yaml to config.yaml."
                )
            mini_cfg = Config.from_yaml(config_path)
            # endregion: init-step-1

            resolved_config = Config.find_config_file(config_path.name) or config_path
            self._init_events.append({
                "n": 1,
                "title": "Load config",
                "summary": f"{config_path.name} · {resolved_config.stat().st_size if resolved_config.exists() else 0} B",
                "files_read": [
                    {
                        "path": _relpath(resolved_config),
                        "size": resolved_config.stat().st_size if resolved_config.exists() else 0,
                        "kind": "yaml",
                    }
                ],
                "files_produced": [],
                "call": {
                    "function": "Config.from_yaml",
                    "module": "mini_agent.config",
                    "source_marker": "init-step-1",
                    "args": {"config_path": _relpath(resolved_config)},
                    "return": {
                        "type": "Config",
                        "fields": _summarize_config(mini_cfg),
                    },
                },
            })
            if on_step_complete:
                await on_step_complete({"n": 1, "event": _enrich_event(self._init_events[-1])})

            # ── Step 2: Build LLM client ───────────────────────────────
            if on_step_start:
                await on_step_start({"n": 2, "title": "Build LLM client"})
            # region: init-step-2
            retry = mini_cfg.llm.retry
            retry_config = RetryConfig(
                enabled=retry.enabled,
                max_retries=retry.max_retries,
                initial_delay=retry.initial_delay,
                max_delay=retry.max_delay,
                exponential_base=retry.exponential_base,
            )
            provider = (
                LLMProvider.ANTHROPIC
                if mini_cfg.llm.provider.lower() == "anthropic"
                else LLMProvider.OPENAI
            )
            # region: init-step-2
            llm_client = LLMClient(
                api_key=mini_cfg.llm.api_key,
                provider=provider,
                api_base=mini_cfg.llm.api_base,
                model=mini_cfg.llm.model,
                retry_config=retry_config if retry.enabled else None,
            )
            # endregion: init-step-2

            self._init_events.append({
                "n": 2,
                "title": "Build LLM client",
                "summary": f"{provider.value} → {llm_client.api_base}",
                "files_read": [],
                "files_produced": [],
                "call": {
                    "function": "LLMClient",
                    "module": "mini_agent.llm",
                    "source_marker": "init-step-2",
                    "args": {
                        "api_key": "***" if mini_cfg.llm.api_key else "(empty)",
                        "provider": provider.value,
                        "api_base": mini_cfg.llm.api_base,
                        "model": mini_cfg.llm.model,
                        "retry_config": (
                            {
                                "enabled": retry.enabled,
                                "max_retries": retry.max_retries,
                                "initial_delay": retry.initial_delay,
                                "max_delay": retry.max_delay,
                                "exponential_base": retry.exponential_base,
                            }
                            if retry.enabled else None
                        ),
                    },
                    "return": {
                        "type": "LLMClient",
                        "fields": {
                            "provider": getattr(llm_client, "provider", None)
                                and str(llm_client.provider).split(".")[-1],
                            "model": llm_client.model,
                            "api_base_normalized": llm_client.api_base,
                        },
                    },
                },
            })
            if on_step_complete:
                await on_step_complete({"n": 2, "event": _enrich_event(self._init_events[-1])})

            # ── Step 3: Build tools ────────────────────────────────────
            if on_step_start:
                await on_step_start({"n": 3, "title": "Build tools"})
            # region: init-step-3
            tools, skill_loader = await initialize_base_tools(mini_cfg)
            base_tool_names = {t.name for t in tools}
            add_workspace_tools(tools, mini_cfg, self._config.workspace_dir)
            # WebUI extension: append the `recall_notes` tool alongside
            # the `record_note` tool that `add_workspace_tools` already
            # registered. The Agent can now actively pull persisted
            # notes back into context instead of only writing them.
            # We guard on `enable_note` so we stay in sync with the
            # upstream toggle — if the user disabled the note tool in
            # config.yaml, neither tool is registered here.
            if mini_cfg.tools.enable_note:
                mem_file = str(self._config.workspace_dir / ".agent_memory.json")
                # `record_note` is already added by add_workspace_tools;
                # only append the missing peer.
                if not any(t.name == "recall_notes" for t in tools):
                    tools.append(RecallNoteTool(memory_file=mem_file))
            for t in tools:
                if t.name in base_tool_names:
                    self._tool_families[t.name] = _classify_tool(t, default="base")
                else:
                    self._tool_families[t.name] = _classify_tool(t, default="workspace")
            self._skill_loader = skill_loader
            # endregion: init-step-3

            # Resolve skills_dir the same way initialize_base_tools does,
            # just for the inspector — never call the loader again.
            skills_dir_path: Optional[Path] = None
            raw_skills = Path(mini_cfg.tools.skills_dir).expanduser()
            if raw_skills.is_absolute():
                skills_dir_path = raw_skills
            else:
                for cand in (
                    raw_skills,
                    Path("mini_agent") / raw_skills,
                    Path(__file__).resolve().parent.parent.parent / "mini_agent" / raw_skills,
                ):
                    if cand.exists():
                        skills_dir_path = cand
                        break
                if skills_dir_path is None:
                    skills_dir_path = raw_skills
            mcp_config_path = Config.find_config_file(mini_cfg.tools.mcp_config_path)

            files_read_step3 = []
            if skills_dir_path is not None:
                files_read_step3.append({
                    "path": _relpath(skills_dir_path),
                    "exists": skills_dir_path.exists(),
                    "kind": "directory",
                })
            if mcp_config_path is not None:
                files_read_step3.append({
                    "path": _relpath(mcp_config_path),
                    "exists": mcp_config_path.exists(),
                    "size": mcp_config_path.stat().st_size if mcp_config_path.exists() else 0,
                    "kind": "json",
                })

            self._init_events.append({
                "n": 3,
                "title": "Build tools",
                "summary": f"{len(tools)} tool(s) across {len(_build_tool_families_summary(tools, self._tool_families))} families",
                "files_read": files_read_step3,
                "files_produced": [],
                "families": _build_tool_families_summary(tools, self._tool_families),
                "call": {
                    "function": "initialize_base_tools + add_workspace_tools",
                    "module": "mini_agent.cli",
                    "source_marker": "init-step-3",
                    "args": {
                        "config.tools.enable_bash": mini_cfg.tools.enable_bash,
                        "config.tools.enable_skills": mini_cfg.tools.enable_skills,
                        "config.tools.enable_mcp": mini_cfg.tools.enable_mcp,
                        "config.tools.enable_file_tools": mini_cfg.tools.enable_file_tools,
                        "config.tools.enable_note": mini_cfg.tools.enable_note,
                        "config.tools.skills_dir": mini_cfg.tools.skills_dir,
                        "config.tools.mcp_config_path": mini_cfg.tools.mcp_config_path,
                        "workspace_dir": _relpath(self._config.workspace_dir),
                    },
                    "return": {
                        "type": "list[Tool]",
                        "fields": {
                            "tools_count": len(tools),
                            "skills_loaded": len(skill_loader.loaded_skills) if skill_loader else 0,
                        },
                    },
                },
            })
            if on_step_complete:
                await on_step_complete({"n": 3, "event": _enrich_event(self._init_events[-1])})

            # ── Step 4: Load system prompt + inject skills metadata ────
            if on_step_start:
                await on_step_start({"n": 4, "title": "Load system prompt + inject skills metadata"})
            # region: init-step-4
            system_prompt_path = Config.find_config_file(
                mini_cfg.agent.system_prompt_path
            )
            if system_prompt_path and system_prompt_path.exists():
                system_prompt = system_prompt_path.read_text(encoding="utf-8")
            else:
                system_prompt = (
                    "You are Mini-Agent, an intelligent assistant powered by "
                    "MiniMax M2.5 that can help users complete various tasks."
                )
            if skill_loader:
                meta = skill_loader.get_skills_metadata_prompt()
                if meta:
                    system_prompt = system_prompt.replace("{SKILLS_METADATA}", meta)
                else:
                    system_prompt = system_prompt.replace("{SKILLS_METADATA}", "")
            else:
                system_prompt = system_prompt.replace("{SKILLS_METADATA}", "")
            # endregion: init-step-4

            skills_metadata_full = (
                skill_loader.get_skills_metadata_prompt() if skill_loader else ""
            )
            self._init_events.append({
                "n": 4,
                "title": "Load system prompt + inject skills metadata",
                "summary": (
                    f"{system_prompt_path.name if system_prompt_path else 'system_prompt.md'} · "
                    f"{len(system_prompt)} chars · "
                    f"{len(skill_loader.loaded_skills) if skill_loader else 0} skill(s) · "
                    f"{len(skills_metadata_full)} chars metadata"
                ),
                "files_read": [
                    {
                        "path": _relpath(system_prompt_path),
                        "exists": True,
                        "size": system_prompt_path.stat().st_size,
                        "kind": "markdown",
                    }
                ] if system_prompt_path and system_prompt_path.exists() else [],
                "files_produced": [],
                "system_prompt": system_prompt,
                "skills_metadata": skills_metadata_full,
                "call": {
                    "function": "SkillLoader.get_skills_metadata_prompt + str.replace",
                    "module": "mini_agent.tools.skill_loader",
                    "source_marker": "init-step-4",
                    "args": {
                        "config.agent.system_prompt_path": mini_cfg.agent.system_prompt_path,
                        "placeholder": "{SKILLS_METADATA}",
                    },
                    "return": {
                        "type": "str",
                        "fields": {
                            "system_prompt_chars": len(system_prompt),
                            "skills_metadata_chars": len(skills_metadata_full),
                            "placeholder_replaced": "{SKILLS_METADATA}" not in system_prompt,
                        },
                    },
                },
            })
            if on_step_complete:
                await on_step_complete({"n": 4, "event": _enrich_event(self._init_events[-1])})

            # ── Step 5: Construct Agent ────────────────────────────────
            if on_step_start:
                await on_step_start({"n": 5, "title": "Construct Agent"})

            # region: init-step-5
            agent = Agent(
                llm_client=llm_client,
                system_prompt=system_prompt,
                tools=tools,
                max_steps=mini_cfg.agent.max_steps,
                workspace_dir=str(self._config.workspace_dir),
            )
            # endregion: init-step-5

            self._init_events.append({
                "n": 5,
                "title": "Construct Agent",
                "summary": f"max_steps={agent.max_steps} · tools={len(agent.tools)} · workspace={self._config.workspace_dir}",
                "files_read": [],
                "files_produced": [
                    {
                        "path": _relpath(self._config.workspace_dir / ".agent_memory.json"),
                        "exists": (self._config.workspace_dir / ".agent_memory.json").exists(),
                        "kind": "json",
                        "note": "懒加载，首次 record_note 时创建",
                    }
                ],
                "call": {
                    "function": "Agent.__init__",
                    "module": "mini_agent.agent",
                    "source_marker": "init-step-5",
                    "args": {
                        "max_steps": mini_cfg.agent.max_steps,
                        "tools_count": len(tools),
                        "system_prompt_chars": len(system_prompt),
                        "workspace_dir": _relpath(self._config.workspace_dir),
                        "token_limit": 80000,
                    },
                    "return": {
                        "type": "Agent",
                        "fields": {
                            "messages_count": len(agent.messages),
                            "tools_dict_size": len(agent.tools),
                            "system_prompt_chars": len(system_prompt),
                            "workspace_dir": str(self._config.workspace_dir),
                        },
                    },
                },
            })
            if on_step_complete:
                await on_step_complete({"n": 5, "event": _enrich_event(self._init_events[-1])})

            self._agent = agent
            self._system_prompt = system_prompt
            self._tool_names = sorted(t.name for t in tools)
            return agent

    async def _replay_init_events(
        self,
        on_step_start: Optional[EventCallback],
        on_step_complete: Optional[EventCallback],
    ) -> None:
        """Replay cached ``_init_events`` through the supplied callbacks.

        Called by ``_ensure_agent`` when the agent was already built by an
        earlier caller (most commonly ``/api/info`` running before
        ``/api/init-trace/stream``). Without this, late SSE consumers
        would see only the final ``done`` event and miss the per-step
        progression that the inspector UI relies on.

        Events are awaited sequentially so the consumer (SSE route) gets
        them one at a time and can yield each to the client before
        moving on. No delay is inserted — the inspector still sees the
        full step-by-step list, just compressed into milliseconds
        because the real work already happened.
        """
        for ev in getattr(self, "_init_events", []):
            if on_step_start:
                await on_step_start({"n": ev["n"], "title": ev["title"]})
            if on_step_complete:
                await on_step_complete({"n": ev["n"], "event": _enrich_event(ev)})

    async def chat(self, message: str) -> dict[str, Any]:
        """Send a message and return the final assistant response.

        Returns a dict with ``reply`` (string) and ``steps`` (int).
        """
        agent = await self._ensure_agent()
        agent.add_user_message(message)
        self._in_flight += 1
        try:
            result = await agent.run()
        finally:
            self._in_flight -= 1
        return {
            "reply": result,
            "steps": len(agent.messages),
            "model": agent.llm.model,
        }

    async def clear(self) -> dict[str, Any]:
        """Reset the conversation history, keeping only the system prompt.

        Mirrors the CLI's ``/clear`` command (see ``mini_agent/cli.py``). What
        this affects:
          - ``Agent.messages`` is reset to ``[system_message]`` — the LLM
            loses its short-term memory for the next call.
        What this does NOT affect:
          - Workspace files (those live on disk, untouched).
          - ``SessionNoteTool`` notes (``workspace/.agent_memory.json``) —
            long-term memory is a separate file and survives clear.
          - LLM client, tool instances, ``max_steps``, ``token_limit``, etc.
          - Background bash processes spawned by earlier ``BashTool`` calls.

        If a run is in flight, this refuses rather than racing with the
        active ``agent.run()`` (which mutates ``messages`` while iterating).
        The frontend should disable the clear button while ``busy``; the
        409 lets it surface a friendly message if the user somehow gets
        there anyway.
        """
        if self._in_flight > 0:
            return {
                "cleared": 0,
                "kept": 1,
                "message_count": -1,
                "busy": True,
            }
        agent = await self._ensure_agent()
        # Guard: messages[0] is always the system prompt (set in Agent.__init__),
        # but be defensive in case a future refactor changes that.
        if not agent.messages or agent.messages[0].role != "system":
            return {
                "cleared": 0,
                "kept": len(agent.messages),
                "message_count": len(agent.messages),
                "busy": False,
            }
        cleared = len(agent.messages) - 1
        agent.messages = [agent.messages[0]]
        return {
            "cleared": cleared,
            "kept": 1,
            "message_count": 1,
            "busy": False,
        }

    def is_ready(self) -> bool:
        """Return True if the agent has finished building.

        Cheap O(1) check used by routes that should NOT trigger init
        (``/api/info``, ``/api/memory/short``, etc.). Returning False
        lets those endpoints answer immediately with an empty/stub
        payload, so the only path that *kicks off* init remains the
        frontend's polling of ``/api/init-trace/state``.
        """
        return self._agent is not None

    async def get_messages(self) -> list:
        """Return a copy of ``agent.messages`` (short-term memory).

        Read-only accessor used by the memory panel route. Returns an
        empty list if the agent hasn't been initialised yet, so the UI
        can render a clean empty state instead of surfacing a 5xx.

        Non-blocking on purpose: this endpoint must NOT kick off init
        on its own. The frontend's ``/api/init-trace/state`` poll is the
        sole entry point for triggering init; if the user opens the page
        and this fires before the polling completes, the panel shows an
        empty list until the polling catches up.
        """
        if not self.is_ready():
            return []
        assert self._agent is not None
        # Return a shallow copy so callers can't mutate the agent's
        # own list (which would race with an in-flight run).
        return list(self._agent.messages)

    def _make_summary_emitter(self, emit: EventCallback) -> EventCallback:
        """Build an ``on_summary`` callback that forwards the agent's
        message-history summarization events to the SSE stream.

        Translates agent-internal dict shapes (``{"event": "round", ...}`` /
        ``{"event": "complete", ...}``) into the web-event vocabulary the
        frontend already consumes, prefixed with ``context_summary_``:

        - ``context_summary_round``  — fired per summarized round
        - ``context_summary_done``   — fired once when the whole pass completes

        The callback swallows its own errors after logging so a frontend
        hiccup can never break the agent loop (mirrors the existing
        ``emit``-error policy elsewhere in this file).
        """

        async def emitter(payload: dict) -> None:
            event = payload.get("event")
            if event == "round":
                await emit({
                    "type": "context_summary_round",
                    "round": payload.get("round"),
                    "compressed_message_count": payload.get("compressed_message_count"),
                    "summary_text": payload.get("summary_text", ""),
                })
            elif event == "complete":
                await emit({
                    "type": "context_summary_done",
                    "before_tokens": payload.get("before_tokens"),
                    "after_tokens": payload.get("after_tokens"),
                    "summary_count": payload.get("summary_count"),
                    "user_round_count": payload.get("user_round_count"),
                    "summaries": payload.get("summaries", []),
                })

        return emitter

    async def chat_streaming(self, message: str, emit: EventCallback) -> str:
        """Run ``agent.run()`` in a background task and translate ``agent.messages``
        growth into step events, without modifying ``Agent`` itself.

        The polling approach (vs. hooks in ``Agent.run``) keeps the core class
        untouched — a single design constraint from PRD §8.3.

        Event sequence for each step:
            step_start → thinking? → content? → tool_call* → tool_result* → step_end

        Plus one ``user`` event before the loop and one ``done`` event after.

        Also surfaces ``context_summary_round`` / ``context_summary_done`` events
        whenever the agent's message-history summarization fires (see
        ``_make_summary_emitter``).
        """
        agent = await self._ensure_agent()
        # Wire the summary emitter for THIS run only. ``chat_streaming`` is
        # serialized via ``_in_flight`` (see ``clear``), so it's safe to
        # overwrite and clear ``on_summary`` around the call. We still reset
        # to None on the way out so a stale emitter can never leak.
        previous_on_summary = agent.on_summary
        agent.on_summary = self._make_summary_emitter(emit)
        try:
            agent.add_user_message(message)
        finally:
            # add_user_message itself never raises, but keep the symmetric
            # restore close to its assignment in case future edits change that.
            pass

        # Snapshot the index BEFORE running; everything past this is new.
        seen = len(agent.messages)  # already includes the user message we added

        await emit({"type": "user", "content": message})

        # Snapshot the index BEFORE running; everything past this is new.
        seen = len(agent.messages)  # already includes the user message we added

        await emit({"type": "user", "content": message})

        # Wrap agent.llm.generate so we can capture the full LLMResponse (finish_reason,
        # usage, raw blocks) for the frontend's ``llm_response`` SSE event. The
        # Message objects in agent.messages only carry content/thinking/tool_calls —
        # everything else is lost after agent.run() returns.
        #
        # Why `tools is not None` to decide whether to record:
        #   - agent.run() main loop (agent.py:372) passes tools=tool_list (non-empty)
        #   - _create_summary (agent.py:302) does NOT pass tools (defaults to None)
        # Recording only main-loop calls keeps the FIFO 1:1 with assistant messages
        # we observe in flush_one().
        original_generate = agent.llm.generate

        # Step lifecycle state shared between wrapped_generate and flush_one.
        # wrapped_generate increments step_num and emits step_start / llm_request
        # BEFORE awaiting the LLM (so the frontend can show a "waiting" state);
        # after the LLM returns it emits llm_response + thinking + content +
        # tool_call events. flush_one uses pending_step_end to know whether a
        # previous step still needs its closing step_end.
        step_num = 0
        step_t0 = perf_counter()
        pending_step_end: Optional[int] = None
        # How many tool_results we're still expecting for the pending step.
        # When this hits zero, the step is genuinely done (all its tool calls
        # have returned) — we emit step_end immediately, instead of waiting
        # for the next LLM call. Without this, the frontend keeps counting
        # time on a step that the backend has actually finished, for the full
        # duration of the next LLM round-trip.
        pending_tool_results: int = 0
        # Maps a tool_call_id → step number it was emitted in. Populated by
        # ``wrapped_generate`` when it fires ``tool_call`` events; consulted
        # by ``flush_one`` when it fires the matching ``tool_result``.
        #
        # Why we need this instead of just using ``step_num``:
        #   - The polling loop and the agent loop run concurrently.
        #   - ``wrapped_generate(N+1)`` starts as soon as step N's tools
        #     finish — it does NOT wait for ``flush_one`` to drain step N's
        #     tool_result messages. So by the time flush_one sees a tool
        #     message, ``step_num`` may already point at N+1.
        #   - Without this map, every tool_result emitted after the step
        #     boundary would carry the wrong ``step`` field, and the
        #     frontend (which keys tool cards by step) would show them in
        #     the wrong card — leaving step N stuck at "⏳ Waiting for
        #     result…" and step N+1 displaying a result it never asked for.
        tool_call_step: dict[str, int] = {}

        async def wrapped_generate(messages, tools=None):
            nonlocal step_num, step_t0, pending_step_end, pending_tool_results

            # Skip summary calls (no tools arg) — they shouldn't trigger
            # step lifecycle events.
            if tools is None:
                return await original_generate(messages, tools)

            # PRE-LLM: announce this step is about to start. The frontend
            # opens the step card here and shows the "waiting" placeholder.
            #
            # Note: we do NOT close any pending previous step here. The
            # pending step is closed by flush_one when it sees the last
            # tool_result for that step. If wrapped_generate(N+1) fires
            # before flush_one has caught up to all tool_messages from
            # step N, the frontend will see step_start(N+1) BEFORE
            # step_end(N) — a minor visual oddity but the data is correct.
            step_num += 1
            step_t0 = perf_counter()
            await emit({
                "type": "step_start",
                "step": step_num,
                "max_steps": agent.max_steps,
            })

            # Show exactly what's about to be sent. ``messages`` is the same
            # list agent.run will pass to the LLM, so the conversion is exact.
            try:
                sys_msg, api_messages = _anthropic_convert_messages_for_inspect(
                    messages
                )
                api_tools = _anthropic_convert_tools_for_inspect(
                    agent.tools.values()
                )
                await emit({
                    "type": "llm_request",
                    "step": step_num,
                    "payload": {
                        "model": agent.llm.model,
                        "max_tokens": 16384,
                        "system": sys_msg,
                        "messages": api_messages,
                        "tools": api_tools,
                    },
                })
            except Exception as exc:
                # Don't break the SSE flow on inspector errors — log and
                # continue with the actual LLM call.
                print(f"[agent_runner] llm_request build failed: {exc}")

            # ACTUAL LLM CALL — this is the only place we hit the network.
            response = await original_generate(messages, tools)

            # POST-LLM: emit the parsed response fields and the raw response
            # in the same order the frontend expects. Raw first so the
            # StepLLMIOPanel can populate finish_reason + usage; then the
            # parsed Thinking/Assistant/Tool cards below.
            try:
                await emit({
                    "type": "llm_response",
                    "step": step_num,
                    "payload": _serialize_llm_response(response),
                })
            except Exception as exc:
                print(f"[agent_runner] llm_response build failed: {exc}")

            if response.thinking:
                await emit({"type": "thinking", "step": step_num, "content": response.thinking})
            if response.content:
                await emit({"type": "content", "step": step_num, "content": response.content})
            if response.tool_calls:
                for tc in response.tool_calls:
                    # Remember which step this tool_call_id belongs to so the
                    # matching tool_result (emitted from the polling loop,
                    # possibly after step_num has already advanced) can be
                    # tagged with the correct step number. See the
                    # ``tool_call_step`` declaration above for the full
                    # rationale.
                    tool_call_step[tc.id] = step_num
                    await emit({
                        "type": "tool_call",
                        "step": step_num,
                        "id": tc.id,
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    })
                pending_step_end = step_num  # close after all tool_results
                pending_tool_results = len(response.tool_calls)
            else:
                # No tool calls → this is the final step; close it immediately.
                await emit({
                    "type": "step_end",
                    "step": step_num,
                    "elapsed_ms": int((perf_counter() - step_t0) * 1000),
                })
                step_t0 = perf_counter()  # reset for next step's safety-net math

            return response

        agent.llm.generate = wrapped_generate

        self._in_flight += 1
        run_task = asyncio.create_task(agent.run())

        async def flush_one() -> bool:
            """Process at most one new message. Return True if anything was emitted.

            Step lifecycle events (step_start, llm_request, llm_response,
            thinking, content, tool_call, step_end-for-final-step) are emitted
            from wrapped_generate. This loop only handles tool_result events
            and the deferred step_end for non-final steps.
            """
            nonlocal pending_step_end, pending_tool_results, step_t0, seen
            if seen >= len(agent.messages):
                return False
            msg = agent.messages[seen]
            seen += 1

            if msg.role == "tool":
                content_str = msg.content if isinstance(msg.content, str) else str(msg.content)
                is_error = content_str.startswith("Error:")
                # Look up which step actually produced this tool call. By
                # the time we get here, ``step_num`` may already point at a
                # later step (the next LLM round-trip may have started
                # before flush_one drained this result), so the closure
                # value would mis-attribute the result to the wrong card.
                # Falling back to ``step_num`` is a defensive last resort
                # for an unrecognised tool_call_id — in practice every id
                # should be in the map.
                result_step = tool_call_step.pop(msg.tool_call_id, step_num)
                await emit({
                    "type": "tool_result",
                    "step": result_step,
                    "id": msg.tool_call_id,
                    "name": msg.name or "?",
                    "success": not is_error,
                    "content": content_str if not is_error else None,
                    "error": content_str if is_error else None,
                })
                # All tool_results for this step are in → close the step now.
                # This is the fix for the "frontend keeps counting time on a
                # finished step" bug: previously we waited for the NEXT
                # assistant message to fire step_end, leaving the user
                # staring at a frozen timer for the full LLM round-trip.
                if pending_step_end is not None and pending_tool_results > 0:
                    pending_tool_results -= 1
                    if pending_tool_results == 0:
                        await emit({
                            "type": "step_end",
                            "step": pending_step_end,
                            "elapsed_ms": int((perf_counter() - step_t0) * 1000),
                        })
                        pending_step_end = None
                        pending_tool_results = 0
                        step_t0 = perf_counter()  # reset for next step's safety-net math
            return True

        # Poll loop: yield events as fast as they appear, with a small floor so
        # we don't burn CPU. 50ms feels real-time to humans (20 fps).
        while not run_task.done():
            await asyncio.sleep(0.05)
            while await flush_one():
                pass

        # Drain any messages appended between the last poll and task completion.
        while await flush_one():
            pass

        # Final step_end if the last step had tool calls but never closed.
        if pending_step_end is not None:
            await emit({
                "type": "step_end",
                "step": pending_step_end,
                "elapsed_ms": int((perf_counter() - step_t0) * 1000),
            })
            pending_step_end = None
            pending_tool_results = 0

        result = await run_task
        # Restore the original generate method so we don't leak the wrapper
        # to subsequent chat_streaming / chat calls (or to anywhere else that
        # uses agent.llm directly, e.g. MemoryPanel via get_messages).
        agent.llm.generate = original_generate
        # Restore the original summary callback (or None if there wasn't one).
        agent.on_summary = previous_on_summary
        self._in_flight -= 1
        await emit({"type": "done", "reply": result, "steps": step_num, "model": agent.llm.model})
        return result

    async def describe(self) -> dict[str, Any]:
        """Return metadata for the /api/info endpoint.

        Non-blocking: returns ``{"ready": False}`` if the agent hasn't
        been built yet, instead of calling ``_ensure_agent`` and racing
        with the frontend's polling-based init trigger. The polling
        ``/api/init-trace/state`` is the sole entry point that should
        ever kick off init; this endpoint must answer immediately so
        that entry point stays unambiguous.
        """
        if not self.is_ready():
            return {"ready": False}
        assert self._agent is not None
        # Display the workspace path relative to the repo root when possible,
        # so the UI header stays short. Fall back to the absolute path if the
        # workspace lives outside the repo.
        repo_root = Path(__file__).resolve().parent.parent.parent
        ws = self._config.workspace_dir.resolve()
        try:
            ws_display = str(ws.relative_to(repo_root))
        except ValueError:
            ws_display = str(ws)
        return {
            "ready": True,
            "model": self._agent.llm.model,
            "api_base": getattr(self._agent.llm, "api_base", None),
            "workspace": ws_display,
            "workspace_abs": str(ws),
            "tools": self._tool_names,
            "max_steps": self._agent.max_steps,
            "message_count": len(self._agent.messages),
        }

    async def init_state(self) -> dict[str, Any]:
        """Snapshot the init pipeline's current state, **without blocking**.

        Powers the frontend's polling-based progress display. Returns
        immediately with whatever events have been captured so far —
        callers see step 1, 2, 3 ... appear one by one as ``_ensure_agent``
        runs, instead of waiting for the whole pipeline (and the
        multi-second step 3 MCP load) to finish.

        The first call kicks off ``_ensure_agent`` in the background if it
        hasn't been started yet. Subsequent calls return whatever new
        events have landed in ``_init_events`` since the last snapshot.

        Returned ``events`` are shallow copies of the cached dicts (with
        source_code enrichment baked in), so the frontend can render
        them directly without waiting for ``done``.
        """
        # 首次调用:kick off _ensure_agent 在后台跑(不传回调,因为
        # 轮询模型不需要 step 回调 —— 每次 poll 直接读 _init_events)
        if self._agent is None and not getattr(self, "_init_state_task", None):
            self._init_state_task = asyncio.create_task(self._ensure_agent())

        events_raw = getattr(self, "_init_events", [])
        # 富化(source_code) + 浅拷贝,避免前端拿到引用后被后续
        # _init_events.append 修改影响
        events = [_enrich_event(ev) for ev in events_raw]

        # 判断哪一步正在跑(用于 loading 动效)
        # - done 数 = len(events)
        # - 下一个 loading = done + 1
        # - 全部 done = len(events) == 5
        all_done = self._agent is not None

        return {
            "ready": all_done,
            "events": events,
            "current_step": len(events) + 1 if not all_done else None,
            "total_steps": 5,
            "model": self._agent.llm.model if all_done else None,
            "api_base": getattr(self._agent.llm, "api_base", None) if all_done else None,
            "workspace": _relpath(self._config.workspace_dir) if all_done else None,
        }

    async def init_trace(self) -> dict[str, Any]:
        """Return a structured trace of the 5-step init pipeline.

        Reads the events captured during the *actual* ``_ensure_agent()``
        call — so the inspector shows the real parameter values that
        were passed and the real source code that ran (read from this
        file's source via region markers), not a static walkthrough.

        Paths are returned relative to the repo root as ``Mini-Agent/...``
        so the UI doesn't leak the user's home directory.

        O(1) after the agent is built — no LLM calls, no tool rebuild.
        """
        try:
            await self._ensure_agent()
        except FileNotFoundError as exc:
            return {"ready": False, "error": str(exc)}

        assert self._agent is not None
        agent = self._agent

        # Enrich each captured event with the actual source code that
        # ran for that step (extracted from this file by region marker).
        # The marker is consumed here so the JSON payload is clean.
        steps_out: list[dict[str, Any]] = []
        for ev in getattr(self, "_init_events", []):
            steps_out.append(_enrich_event(ev))

        return {
            "ready": True,
            "model": agent.llm.model,
            "api_base": getattr(agent.llm, "api_base", None),
            "provider": str(getattr(agent.llm, "provider", "")).lower(),
            "workspace": _relpath(self._config.workspace_dir),
            "steps": steps_out,
        }


# ---------------------------------------------------------------------------
# Module-level singleton accessor
# ---------------------------------------------------------------------------

_runner: Optional[AgentRunner] = None


def get_runner() -> AgentRunner:
    """Return the process-wide AgentRunner (created on first call)."""
    global _runner
    if _runner is None:
        _runner = AgentRunner(load_web_config())
    return _runner