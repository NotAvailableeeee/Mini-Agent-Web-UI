"""POST /api/chat — send a message, run the Agent, return the response.
POST /api/chat/stream — same, but with step-by-step SSE events.
POST /api/chat/clear — reset the Agent's conversation history (mirrors CLI /clear).

M1: synchronous request/response. No streaming, no cancellation.
M2 will add WebSocket streaming.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..agent_runner import get_runner

router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="User message")


class ChatResponse(BaseModel):
    reply: str
    steps: int
    model: str
    error: Optional[str] = None


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """Send a single message to the Agent and return its final reply.

    Conversation history is preserved across calls (the Agent keeps state in
    memory); this matches the CLI's multi-turn behaviour.
    """
    try:
        runner = get_runner()
        result = await runner.chat(req.message)
        return ChatResponse(**result)
    except FileNotFoundError as exc:
        # Configuration file missing — surface as 503 to make it actionable.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}") from exc


@router.post("/clear")
async def chat_clear() -> JSONResponse:
    """Reset the Agent's conversation history (mirrors the CLI's ``/clear``).

    Keeps only the system prompt — the next LLM call starts with a clean
    short-term memory. Long-term notes (``workspace/.agent_memory.json``)
    and workspace files are untouched.

    Returns 409 if a run is in flight, since concurrent mutation of
    ``Agent.messages`` would race with the active ``agent.run()``. The
    frontend disables the button while ``busy``, so this is a defensive
    guard rather than the normal path.
    """
    runner = get_runner()
    try:
        result = await runner.clear()
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=f"Clear failed: {exc}") from exc
    if result.get("busy"):
        return JSONResponse(
            status_code=409,
            content={
                "detail": "Agent is currently running. Wait for it to finish before clearing.",
                **result,
            },
        )
    return JSONResponse(result)


def _sse(event: dict) -> bytes:
    """Format a single Server-Sent Event. One JSON object per chunk."""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")


@router.post("/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    """Send a message and stream step events back as SSE.

    Events emitted (see ``AgentRunner.chat_streaming``):
      - user            : echoed back, lets the UI mark the input as sent
      - step_start      : a new step begins; UI should auto-collapse previous
      - thinking        : model thinking block (may be empty)
      - content         : model text output for the step
      - tool_call       : tool invocation requested by the model
      - tool_result     : tool execution outcome
      - step_end        : step finished (with elapsed_ms)
      - done            : final reply + total step count
      - error           : {message: str}; the stream is closed after this
    """

    async def event_source() -> AsyncIterator[bytes]:
        # Bridge an async callback into the SSE byte stream via a queue so the
        # agent loop never blocks on the network.
        queue: asyncio.Queue[Optional[dict]] = asyncio.Queue()

        async def emit(event: dict) -> None:
            await queue.put(event)

        async def run() -> None:
            try:
                runner = get_runner()
                await runner.chat_streaming(req.message, emit)
            except FileNotFoundError as exc:
                await queue.put({"type": "error", "message": str(exc)})
            except Exception as exc:  # pragma: no cover - defensive
                await queue.put({"type": "error", "message": f"Agent error: {exc}"})
            finally:
                await queue.put(None)  # sentinel → end of stream

        agent_task = asyncio.create_task(run())

        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield _sse(event)
        finally:
            # If the client disconnected, ensure we don't leak the agent task.
            if not agent_task.done():
                agent_task.cancel()
                try:
                    await agent_task
                except (asyncio.CancelledError, Exception):
                    pass

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering, if proxied
            "Connection": "keep-alive",
        },
    )