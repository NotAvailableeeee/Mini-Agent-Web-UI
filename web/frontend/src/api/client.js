/**
 * API client — wraps fetch() with sensible defaults.
 * Base URL is empty in dev (Vite proxies /api) and empty in prod (same origin).
 */

const BASE = ''

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })
  if (!res.ok) {
    let detail
    try { detail = (await res.json()).detail } catch { detail = res.statusText }
    throw new Error(detail || `HTTP ${res.status}`)
  }
  return res.json()
}

/**
 * Read a POST /api/chat/stream response as an async iterable of event objects.
 * Each `data: {...}` SSE frame is yielded as a parsed object. Caller drives
 * the loop via `for await` — `yield`ing control back to the event loop lets
 * Vue render between events.
 */
async function* ssePost(path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok || !res.body) {
    let detail
    try { detail = (await res.json()).detail } catch { detail = res.statusText }
    throw new Error(detail || `HTTP ${res.status}`)
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder('utf-8')
  // SSE frames are separated by a blank line. We accumulate across reads
  // because a single read() may split or merge frames arbitrarily.
  let buf = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let sep
    while ((sep = buf.indexOf('\n\n')) !== -1) {
      const frame = buf.slice(0, sep)
      buf = buf.slice(sep + 2)
      const line = frame.split('\n').find((l) => l.startsWith('data: '))
      if (line) {
        try {
          yield JSON.parse(line.slice(6))
        } catch {
          // ignore malformed frame
        }
      }
    }
  }
}

export const api = {
  health: () => request('/api/health'),
  info: () => request('/api/info'),
  // Agent initialization pipeline trace. Powers the 🔍 header modal
  // (AgentInitInspector.vue). Returns `{ready: false, error: ...}` if
  // the runner hasn't been built yet / config is missing, mirroring
  // `/api/info`'s soft-error pattern.
  initTrace: () => request('/api/init-trace'),
  /**
   * Snapshot the current init state — non-blocking.
   *
   * Used by the inspector's polling fallback. The backend's
   * `AgentRunner.init_state` kicks off `_ensure_agent` on the first
   * call and returns whatever events have been captured so far.
   * Each subsequent call returns incrementally more events as the
   * pipeline runs. We poll every ~200ms while loading so the
   * inspector shows step-by-step progress without depending on SSE
   * (which gets buffered by the Vite dev proxy).
   */
  initTraceState: () => request('/api/init-trace/state'),
  /**
   * Subscribe to the SSE stream of init-pipeline progress.
   *
   * The backend's `_ensure_agent` invokes an `on_step_start` callback
   * just before each step's code runs, pushing the title through a
   * queue into the SSE response. This consumer reads the stream and
   * dispatches each event to `onEvent`. Yields control (and resolves)
   * once the `done` event arrives.
   *
   * Returns a promise that resolves with the final trace object
   * (identical to `initTrace()`'s payload), or rejects on transport
   * error / `error` event.
   */
  initTraceStream: (onEvent) =>
    (async () => {
      const res = await fetch('/api/init-trace/stream', {
        headers: { Accept: 'text/event-stream' },
      })
      if (!res.ok || !res.body) {
        throw new Error(`init-trace stream HTTP ${res.status}`)
      }
      const reader = res.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buf = ''
      let finalTrace = null
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        // SSE frames are separated by a blank line. A single read()
        // may split or merge frames arbitrarily, so loop on the buffer.
        let sep
        while ((sep = buf.indexOf('\n\n')) !== -1) {
          const frame = buf.slice(0, sep)
          buf = buf.slice(sep + 2)
          const line = frame.split('\n').find((l) => l.startsWith('data: '))
          if (!line) continue
          let event
          try { event = JSON.parse(line.slice(6)) } catch { continue }
          onEvent(event)
          if (event.type === 'done') {
            finalTrace = event
            return finalTrace
          }
          if (event.type === 'error') {
            throw new Error(event.error || 'init-trace stream error')
          }
        }
      }
      return finalTrace
    })(),
  listCards: () => request('/api/showcase/cards'),
  chat: (message) =>
    request('/api/chat', { method: 'POST', body: JSON.stringify({ message }) }),
  chatStream: (message, onEvent) =>
    // Iterate the SSE stream and dispatch each event to the caller. Returns
    // a promise that resolves when the `done` (or `error`) event arrives.
    (async () => {
      for await (const event of ssePost('/api/chat/stream', { message })) {
        onEvent(event)
        if (event.type === 'done' || event.type === 'error') return event
      }
    })(),
  clearChat: () =>
    // Reset the Agent's conversation history on the server. The backend may
    // return 409 if a run is in flight — `request()` re-throws that as an
    // Error whose `.message` is the FastAPI `detail`, which the caller
    // surfaces to the user. Returns `{ cleared, kept, message_count, busy }`
    // on success.
    request('/api/chat/clear', { method: 'POST' }),
  // Workspace file browser — see backend/routes/workspace.py.
  // `path` is relative to the workspace root and must not escape it.
  workspaceTree: () => request('/api/workspace/tree'),
  workspaceFile: (path) =>
    request(`/api/workspace/file?path=${encodeURIComponent(path)}`),
  // Memory panel — see backend/routes/memory.py.
  // short = agent.messages (the LLM's working context for the next
  // turn, minus the system prompt). long = workspace/.agent_memory.json
  // notes written by the record_note tool. graph = workspace/.mcp_memory.jsonl
  // entities+relations written by the memory MCP server.
  memoryShort: () => request('/api/memory/short'),
  memoryLong: () => request('/api/memory/long'),
  memoryGraph: () => request('/api/memory/graph'),
}