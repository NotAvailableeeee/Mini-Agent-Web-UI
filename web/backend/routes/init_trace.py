"""GET /api/init-trace — Agent initialization pipeline trace for the inspector modal.

The frontend opens this via the 🔍 button in the header and renders a
5-card accordion (Load config → LLM client → Tools → System prompt →
Construct Agent) with file/code references and rendered markdown bodies.

All heavy lifting happens in ``AgentRunner.init_trace()``. The route
here is intentionally thin so it can be reasoned about at a glance.

GET /api/init-trace/stream — SSE stream of init progress.
Each step's title is yielded as ``step_start`` event the moment that
step's code begins running on the backend (via the ``on_step_start``
callback wired into ``AgentRunner._ensure_agent``); the full
``step_complete`` payload (files / call args / return value / source
code) follows the moment the step's event is captured. The final
``done`` event carries the full trace JSON, identical to the
non-streaming endpoint's payload. Errors surface as ``error`` events
so the frontend never silently hangs.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ..agent_runner import get_runner

router = APIRouter()


@router.get("")
async def init_trace() -> dict[str, Any]:
    """Return the init trace, or a soft error if config is missing."""
    try:
        runner = get_runner()
        return await runner.init_trace()
    except Exception as exc:  # pragma: no cover - defensive
        # Mirror /api/info's pattern: never 5xx for a missing config —
        # let the modal render an error state instead.
        return {"ready": False, "error": f"trace failed: {exc}"}


@router.get("/state")
async def init_trace_state() -> dict[str, Any]:
    """Snapshot the init pipeline's current state — non-blocking.

    Powers the frontend's polling progress display. Kicks off
    ``_ensure_agent`` on the first call (in the background) and returns
    whatever events have been captured so far. Subsequent calls return
    incrementally more events as ``_ensure_agent`` runs, so the frontend
    sees step 1, 2, 3 ... appear one by one instead of waiting for the
    whole pipeline (most notably the multi-second step 3 MCP load) to
    finish.

    This endpoint exists to sidestep the dev-mode Vite proxy's
    HTTP-response buffering, which silently swallows real-time SSE
    updates and made the inspector show "stuck at step 1, then all
    done at once". HTTP polling short-circuits that buffering entirely:
    every poll is an independent request with its own short response,
    impossible to coalesce.
    """
    try:
        runner = get_runner()
        return await runner.init_state()
    except Exception as exc:  # pragma: no cover - defensive
        return {"ready": False, "error": f"state failed: {exc}"}


def _sse(event: dict[str, Any]) -> bytes:
    """Encode one SSE event. ``data:`` line + trailing blank line."""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")


@router.get("/stream")
async def init_trace_stream() -> StreamingResponse:
    """SSE stream that emits each init step the moment it starts.

    Protocol (one ``data:`` line per event, blank line terminator):

    - ``{"type": "step_start", "n": 1, "title": "Load config"}``
      emitted by the ``on_step_start`` callback wired into
      ``_ensure_agent`` — true real-time sync.
    - ``{"type": "step_complete", "n": 1, "event": {...}}`` emitted
      by the ``on_step_complete`` callback right after each step's
      data is captured. ``event`` carries the full step payload
      (files, call args, return value, source code) so the frontend
      can unlock that step's tab *progressively* — done tabs become
      clickable as their data arrives, instead of waiting for ``done``.
    - ``{"type": "done", "ready": true, "model": ..., "steps": [...]}``
      after init completes — identical payload to ``GET /api/init-trace``.
    - ``{"type": "error", "error": "..."}`` if init raises.

    The SSE queue + background task pattern keeps the runner's natural
    progression on the asyncio event loop (the callback awaits the
    queue.put, so other coroutines can run between steps). Streaming
    ends with the runner either completing or raising.
    """
    runner = get_runner()
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def run_init() -> None:
        try:
            async def on_step_start(payload: dict[str, Any]) -> None:
                await queue.put({"type": "step_start", **payload})

            async def on_step_complete(payload: dict[str, Any]) -> None:
                await queue.put({"type": "step_complete", **payload})

            await runner._ensure_agent(
                on_step_start=on_step_start,
                on_step_complete=on_step_complete,
            )
            trace = await runner.init_trace()
            await queue.put({"type": "done", **trace})
        except Exception as exc:
            await queue.put({"type": "error", "error": str(exc)})
        finally:
            # Sentinel: tell the generator to stop.
            await queue.put(None)

    async def event_gen() -> AsyncIterator[bytes]:
        # Kick off init in the background; the queue feeds events back here.
        init_task = asyncio.create_task(run_init())
        try:
            while True:
                event = await queue.get()
                if event is None:  # sentinel
                    break
                yield _sse(event)
                # 关键:小停顿,迫使 uvicorn 的 HTTP transport 立即
                # flush 上一个 chunk。否则小步事件会被 socket 层合
                # 并成批,前端要等几秒(step 3 跑完)才能看到首批
                # step_complete —— 用户看到的现象就是"卡在 step 1
                # 然后突然全完成"。
                # 30ms 兼顾:足够让 transport drain、不让用户感觉卡顿。
                await asyncio.sleep(0.03)
        finally:
            # Make sure we don't leak the task if the client disconnects.
            if not init_task.done():
                init_task.cancel()
                try:
                    await init_task
                except (asyncio.CancelledError, Exception):
                    pass

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering, if any
            "Connection": "keep-alive",
        },
    )
