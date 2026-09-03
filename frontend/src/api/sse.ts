/**
 * SSE client for POST /api/chat. Owner: person 1.
 *
 * fetch + ReadableStream rather than EventSource, because /api/chat is a POST
 * with a JSON body and EventSource can only GET.
 *
 * The parser is deliberately strict about frame boundaries: a token frame can
 * arrive split across two network chunks, and a naive line-splitter drops it.
 */
import type { Attachment, Event } from '../types/events';
import { API_BASE } from './rest';

/** Frames are separated by a blank line; a \r\n server is still legal SSE. */
const FRAME_SEP = /\r?\n\r?\n/;

export interface StreamHandle {
  /** Resolves when the stream ends, for any reason. */
  done: Promise<void>;
  /** Aborts the fetch. The server is told separately via cancelChat(). */
  abort: () => void;
}

export function streamChat(
  body: {
    session_id?: string | null;
    message: string;
    /** Run this turn on a chosen model. Omit to let the router decide. */
    model_id?: string | null;
    /** Staged via /api/upload; an image here routes the turn to vision. */
    attachments?: Attachment[];
  },
  onEvent: (ev: Event) => void,
  onFatal: (message: string) => void,
): StreamHandle {
  const controller = new AbortController();

  const done = (async () => {
    let res: Response;
    try {
      res = await fetch(`${API_BASE}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // Built field by field on purpose, so nothing the caller happens to
        // be carrying leaks onto the wire. That also means a new field is
        // silently dropped until it is named here -- which is exactly what
        // happened to model_id: the type accepted it, the request never
        // carried it, and the override looked wired while doing nothing.
        body: JSON.stringify({
          session_id: body.session_id ?? null,
          message: body.message,
          attachments: body.attachments ?? [],
          ...(body.model_id ? { model_id: body.model_id } : {}),
        }),
        signal: controller.signal,
      });
    } catch (err) {
      if (!controller.signal.aborted) {
        onFatal(`cannot reach the backend: ${describe(err)}`);
      }
      return;
    }

    if (!res.ok || !res.body) {
      // A refused turn -- an unknown model, or one that cannot do this job --
      // comes back as 400 with a `detail` that says which and suggests what to
      // pick instead. Showing "backend returned 400 Bad Request" instead of
      // that sentence throws away the only useful part of the response.
      let detail = '';
      try {
        const body = (await res.json()) as { detail?: unknown };
        if (typeof body?.detail === 'string') detail = body.detail;
      } catch {
        /* not JSON: fall back to the status line */
      }
      onFatal(detail || `backend returned ${res.status} ${res.statusText}`);
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    try {
      for (;;) {
        const { done: finished, value } = await reader.read();
        if (finished) break;
        buf += decoder.decode(value, { stream: true });

        for (;;) {
          const match = FRAME_SEP.exec(buf);
          if (!match) break;
          const frame = buf.slice(0, match.index);
          buf = buf.slice(match.index + match[0].length);
          emit(frame, onEvent, onFatal);
        }
      }
      // A server that closes without a trailing blank line still owes us the
      // last frame.
      if (buf.trim()) emit(buf, onEvent, onFatal);
    } catch (err) {
      if (!controller.signal.aborted) {
        onFatal(`stream broke: ${describe(err)}`);
      }
    }
  })();

  return { done, abort: () => controller.abort() };
}

function emit(
  frame: string,
  onEvent: (ev: Event) => void,
  onFatal: (message: string) => void,
): void {
  // Per the SSE spec a frame may carry several data: lines; concatenate them.
  const data = frame
    .split(/\r?\n/)
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trim())
    .join('\n');
  if (!data) return;

  let parsed: unknown;
  try {
    parsed = JSON.parse(data);
  } catch {
    onFatal(`backend sent a frame that is not JSON: ${data.slice(0, 120)}`);
    return;
  }
  if (!isEvent(parsed)) {
    // An unknown `type` means backend and frontend have drifted. Say so loudly
    // rather than dropping it -- a silently ignored event is a demo-day mystery.
    const type = (parsed as { type?: unknown })?.type;
    onFatal(`unknown event type from backend: ${JSON.stringify(type)}`);
    return;
  }
  onEvent(parsed);
}

/** The twelve types in the contract. Nothing else is accepted. */
const EVENT_TYPES = new Set([
  'session.start', 'router.decision', 'model.loading', 'model.ready',
  'agent.step', 'token', 'tool.call', 'tool.result', 'citation',
  'artifact', 'error', 'done',
]);

function isEvent(value: unknown): value is Event {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as { type?: unknown }).type === 'string' &&
    EVENT_TYPES.has((value as { type: string }).type)
  );
}

function describe(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
