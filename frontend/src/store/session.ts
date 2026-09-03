/**
 * Event reducer and client state. Owner: person 1.
 *
 * Every panel reads from here. The reducer handles all twelve contract event
 * types exhaustively -- `assertNever` at the bottom of the switch makes an
 * unhandled type a COMPILE error, so adding an event to the contract cannot
 * silently no-op in the UI.
 *
 * No localStorage, no sessionStorage. State lives here and dies with the tab.
 */
import { create } from 'zustand';
import type {
  Artifact, Citation, Event, ModelInfo, RouterDecision, StopReason, TaskType,
} from '../types/events';
import { cancelChat, models as fetchModels } from '../api/rest';
import { streamChat } from '../api/sse';

// --- view models -----------------------------------------------------------

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  /** Set once `done` arrives, so the bubble can show why it stopped. */
  stopReason?: StopReason;
  citations: Citation[];
  ts: number;
}

export interface TraceStep {
  step: number;
  callId: string | null;
  tool: string | null;
  args: Record<string, unknown>;
  ok: boolean | null;          // null while the call is still running
  summary: string | null;
  durationMs: number | null;
  truncated: boolean;
  startedAt: number;
}

export interface SwapState {
  modelId: string;
  evicting: string | null;
  etaS: number;
  startedAt: number;
}

export interface RunStats {
  stopReason: StopReason;
  stepsUsed: number;
  tokensIn: number;
  tokensOut: number;
  latencyMs: number;
}

export interface StreamError {
  code: string;
  message: string;
  recoverable: boolean;
  ts: number;
}

type Phase = 'idle' | 'waiting' | 'routing' | 'swapping' | 'streaming';

interface SessionState {
  // stream
  phase: Phase;
  sessionId: string | null;
  messages: ChatMessage[];
  // panels
  router: RouterDecision | null;
  swap: SwapState | null;
  activeModel: string | null;
  modelVramMb: number | null;
  step: number;
  maxSteps: number;
  trace: TraceStep[];
  artifacts: Artifact[];
  citations: Citation[];
  errors: StreamError[];
  lastRun: RunStats | null;
  // model picker
  models: ModelInfo[];
  /** null means "let the router choose", which is the default behaviour. */
  selectedModel: string | null;
  // actions
  send: (message: string) => void;
  stop: () => void;
  clear: () => void;
  /** Surface a client-side failure in the same place stream errors appear. */
  pushError: (message: string, code?: string) => void;
  loadModels: () => void;
  selectModel: (modelId: string | null) => void;
}

const EMPTY = {
  phase: 'idle' as Phase,
  sessionId: null,
  messages: [] as ChatMessage[],
  router: null,
  swap: null,
  activeModel: null,
  modelVramMb: null,
  step: 0,
  maxSteps: 0,
  trace: [] as TraceStep[],
  artifacts: [] as Artifact[],
  citations: [] as Citation[],
  errors: [] as StreamError[],
  lastRun: null,
};

// Deliberately outside EMPTY: `clear()` wipes the conversation, not the user's
// choice of model or the registry we fetched to render it.
const MODEL_DEFAULTS = {
  models: [] as ModelInfo[],
  selectedModel: null as string | null,
};

/** Live stream handle. Outside the store: it is not state, it is a resource. */
let active: { abort: () => void } | null = null;

export const useSession = create<SessionState>((set, get) => ({
  ...EMPTY,
  ...MODEL_DEFAULTS,

  send(message: string) {
    const text = message.trim();
    if (!text || get().phase !== 'idle') return;

    const now = Date.now();
    set((s) => ({
      phase: 'waiting',
      // A new turn resets the per-turn panels but keeps the conversation.
      router: null,
      swap: null,
      step: 0,
      maxSteps: 0,
      trace: [],
      lastRun: null,
      messages: [
        ...s.messages,
        { id: `u${now}`, role: 'user', content: text, citations: [], ts: now },
        { id: `a${now}`, role: 'assistant', content: '', citations: [], ts: now },
      ],
    }));

    active = streamChat(
      {
        session_id: get().sessionId,
        message: text,
        // Omitted entirely when nothing is picked, so the request is byte for
        // byte what it was before the picker existed and the router behaves
        // exactly as it did.
        ...(get().selectedModel ? { model_id: get().selectedModel } : {}),
      },
      (ev) => set((s) => reduce(s, ev)),
      (message: string) =>
        set((s) => ({
          phase: 'idle',
          trace: freezeOpenSteps(s.trace),
          errors: [
            ...s.errors,
            { code: 'TRANSPORT', message, recoverable: false, ts: Date.now() },
          ],
        })),
    );
  },

  stop() {
    const { sessionId, phase } = get();
    if (phase === 'idle') return;
    // Tell the server first so it can stop generating, then drop the socket.
    if (sessionId) void cancelChat(sessionId).catch(() => undefined);
    active?.abort();
    active = null;
    set((s) => ({
      phase: 'idle',
      swap: null,
      trace: freezeOpenSteps(s.trace),
      lastRun: s.lastRun ?? {
        stopReason: 'cancelled', stepsUsed: s.step,
        tokensIn: 0, tokensOut: 0, latencyMs: 0,
      },
      messages: markLastAssistant(s.messages, (m) => ({
        ...m, stopReason: m.stopReason ?? 'cancelled',
      })),
    }));
  },

  clear() {
    active?.abort();
    active = null;
    set({ ...EMPTY });
  },

  pushError(message: string, code = 'CLIENT') {
    set((s) => ({
      errors: [...s.errors, { code, message, recoverable: true, ts: Date.now() }],
    }));
  },

  loadModels() {
    void fetchModels()
      .then((models) => set({ models }))
      // A picker that cannot reach /api/models is a disabled picker, not a
      // crash: the composer still sends, and the router still routes.
      .catch(() => set({ models: [] }));
  },

  selectModel(modelId: string | null) {
    set({ selectedModel: modelId });
  },
}));

// --- the reducer -----------------------------------------------------------

function reduce(s: SessionState, ev: Event): Partial<SessionState> {
  switch (ev.type) {
    case 'session.start':
      return { sessionId: ev.session_id, phase: 'routing' };

    case 'router.decision':
      return { router: ev, phase: 'routing' };

    case 'model.loading':
      // The swap is a feature. Record when it started so the panel can count up.
      return {
        phase: 'swapping',
        swap: {
          modelId: ev.model_id,
          evicting: ev.evicting ?? null,
          etaS: ev.eta_s,
          startedAt: Date.now(),
        },
      };

    case 'model.ready':
      return {
        phase: 'streaming',
        swap: null,
        activeModel: ev.model_id,
        modelVramMb: ev.vram_mb,
        // The card holds one model at a time, so a ready event is also the
        // authoritative answer to "which one is resident right now". Keeping
        // the picker in step here means it never has to re-poll /api/models to
        // find out what it just watched load.
        models: s.models.map((m) => ({ ...m, loaded: m.id === ev.model_id })),
      };

    case 'agent.step':
      return {
        phase: 'streaming',
        step: ev.step,
        maxSteps: ev.max_steps,
        trace: [
          ...s.trace,
          {
            step: ev.step, callId: null, tool: null, args: {}, ok: null,
            summary: null, durationMs: null, truncated: false,
            startedAt: Date.now(),
          },
        ],
      };

    case 'token':
      return {
        phase: 'streaming',
        messages: markLastAssistant(s.messages, (m) => ({
          ...m, content: m.content + ev.text,
        })),
      };

    case 'tool.call': {
      // Attach to the open step if there is one; otherwise open a bare row, so
      // a tool call that arrives without a preceding agent.step is still shown.
      const trace = [...s.trace];
      const idx = lastIndexWhere(trace, (t) => t.callId === null);
      const row: TraceStep = {
        step: idx >= 0 ? trace[idx].step : s.step,
        callId: ev.call_id,
        tool: ev.name,
        args: ev.args ?? {},
        ok: null, summary: null, durationMs: null, truncated: false,
        startedAt: idx >= 0 ? trace[idx].startedAt : Date.now(),
      };
      if (idx >= 0) trace[idx] = row;
      else trace.push(row);
      return { trace };
    }

    case 'tool.result': {
      const trace = [...s.trace];
      const idx = lastIndexWhere(trace, (t) => t.callId === ev.call_id);
      if (idx < 0) return {};   // result for a call we never saw; ignore
      trace[idx] = {
        ...trace[idx],
        ok: ev.ok,
        summary: ev.summary,
        durationMs: ev.duration_ms,
        truncated: ev.truncated,
      };
      return { trace };
    }

    case 'citation':
      return {
        citations: [...s.citations, ev],
        messages: markLastAssistant(s.messages, (m) => ({
          ...m, citations: [...m.citations, ev],
        })),
      };

    case 'artifact':
      return { artifacts: [...s.artifacts, ev] };

    case 'error':
      // A recoverable error is not the end of the run -- the agent retries and
      // more events follow. Only an unrecoverable one stops the stream.
      return {
        errors: [
          ...s.errors,
          {
            code: ev.code, message: ev.message,
            recoverable: ev.recoverable, ts: Date.now(),
          },
        ],
        phase: ev.recoverable ? s.phase : 'idle',
        trace: ev.recoverable ? s.trace : freezeOpenSteps(s.trace),
      };

    case 'done':
      active = null;
      return {
        phase: 'idle',
        swap: null,
        trace: freezeOpenSteps(s.trace),
        lastRun: {
          stopReason: ev.stop_reason,
          stepsUsed: ev.steps_used,
          tokensIn: ev.tokens_in,
          tokensOut: ev.tokens_out,
          latencyMs: ev.latency_ms,
        },
        messages: markLastAssistant(s.messages, (m) => ({
          ...m, stopReason: ev.stop_reason,
        })),
      };

    default:
      return assertNever(ev);
  }
}

// --- helpers ---------------------------------------------------------------

/**
 * A run can end with a step still open -- a fatal error, a cancel, or a
 * backend that dies mid-tool. Freeze those rows at their elapsed time so the
 * live counter stops; leaving it ticking forever says "still working" about a
 * run that is over.
 */
function freezeOpenSteps(trace: TraceStep[]): TraceStep[] {
  const now = Date.now();
  return trace.map((t) =>
    t.ok === null && t.durationMs === null
      ? { ...t, durationMs: now - t.startedAt }
      : t,
  );
}

function markLastAssistant(
  messages: ChatMessage[],
  update: (m: ChatMessage) => ChatMessage,
): ChatMessage[] {
  const idx = lastIndexWhere(messages, (m) => m.role === 'assistant');
  if (idx < 0) return messages;
  const next = [...messages];
  next[idx] = update(next[idx]);
  return next;
}

function lastIndexWhere<T>(items: T[], pred: (item: T) => boolean): number {
  for (let i = items.length - 1; i >= 0; i--) if (pred(items[i])) return i;
  return -1;
}

/**
 * Makes the switch above exhaustive. If someone adds a thirteenth event type to
 * contracts/ and regenerates, THIS LINE stops compiling. That is the point of
 * the whole contracts package.
 */
function assertNever(value: never): never {
  throw new Error(`unhandled event: ${JSON.stringify(value)}`);
}

// --- selectors used by more than one panel ---------------------------------

export const isBusy = (s: SessionState): boolean => s.phase !== 'idle';

export const taskTypeLabel: Record<TaskType, string> = {
  general: 'GENERAL', code: 'CODE', document: 'DOCUMENT',
  vision: 'VISION', data: 'DATA',
};
