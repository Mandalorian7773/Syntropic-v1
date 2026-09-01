/**
 * SSE client. Owner: person 1.
 *
 * Stub: opens POST /api/chat, parses `data:` lines into contract events, calls
 * back per frame. Reconnect, cancellation, backpressure and per-event routing
 * into the store are person 1's to build.
 *
 * Uses fetch + ReadableStream rather than EventSource because /api/chat is a
 * POST with a JSON body. EventSource can only GET.
 */
import type { ChatRequest, Event } from '../types/events';

export async function streamChat(
  body: ChatRequest,
  onEvent: (ev: Event) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.body) throw new Error('no response body');

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line.
    let sep: number;
    while ((sep = buf.indexOf('\n\n')) !== -1) {
      const frame = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      for (const line of frame.split('\n')) {
        if (!line.startsWith('data:')) continue;
        onEvent(JSON.parse(line.slice(5).trim()) as Event);
      }
    }
  }
}
