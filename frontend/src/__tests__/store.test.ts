/**
 * Drives the real store through the real SSE client against the real mock, so
 * what the panels read is what actually comes off the wire.
 */
import { type ChildProcess } from 'node:child_process';
import { startMock } from './mockServer';
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { useSession } from '../store/session';

const PORT = 8178;
let proc: ChildProcess;

beforeAll(async () => {
  proc = await startMock(PORT);
}, 30000);

afterAll(() => proc?.kill());
beforeEach(() => useSession.getState().clear());

/** Send, then wait for the store to settle back to idle. */
async function send(message: string) {
  useSession.getState().send(message);
  for (let i = 0; i < 400; i++) {
    if (useSession.getState().phase === 'idle' &&
        useSession.getState().lastRun) return useSession.getState();
    await new Promise((r) => setTimeout(r, 25));
  }
  throw new Error(`stream never finished: phase=${useSession.getState().phase}`);
}

describe('store reduces a full run', () => {
  it('document run fills every panel', async () => {
    const s = await send('what does the SOP say about wall loss');

    expect(s.sessionId).toBeTruthy();
    expect(s.router?.task_type).toBe('document');
    expect(s.activeModel).toBe('qwen2.5-vl-7b');

    // Chat: user message then a non-empty assistant message.
    expect(s.messages.map((m) => m.role)).toEqual(['user', 'assistant']);
    expect(s.messages[1].content).toContain('Finding');
    expect(s.messages[1].stopReason).toBe('final_answer');

    // Trace: three steps, each resolved with a tool, ok and a duration.
    expect(s.trace).toHaveLength(3);
    expect(s.trace.map((t) => t.tool))
      .toEqual(['read_document', 'search_documents', 'create_docx']);
    for (const t of s.trace) {
      expect(t.ok).toBe(true);
      expect(t.durationMs).toBeGreaterThan(0);
      expect(Object.keys(t.args).length).toBeGreaterThan(0);
    }

    // Citations land on the assistant message AND the session list.
    expect(s.citations).toHaveLength(3);
    expect(s.messages[1].citations).toHaveLength(3);

    expect(s.artifacts).toHaveLength(1);
    expect(s.artifacts[0].filename).toBe('approval-note.docx');
    expect(s.lastRun?.stepsUsed).toBe(3);
  });

  it('code run records the swap and the failed-then-ok tool pair', async () => {
    const s = await send('write a python script for downtime');
    expect(s.activeModel).toBe('qwen2.5-coder-7b');
    // The swap is cleared by model.ready; the run ends with no swap pending.
    expect(s.swap).toBeNull();
    expect(s.trace.map((t) => t.ok)).toEqual([false, true]);
    expect(s.trace[0].summary).toContain('KeyError');
  });

  it('failure run keeps the error and still reaches a final answer', async () => {
    const s = await send('scan every page, make this fail');
    expect(s.errors).toHaveLength(1);
    expect(s.errors[0].code).toBe('TOOL_TIMEOUT');
    expect(s.errors[0].recoverable).toBe(true);
    // Recoverable error must NOT end the run.
    expect(s.lastRun?.stopReason).toBe('final_answer');
    expect(s.trace).toHaveLength(3);
  });

  it('simple run produces prose and no trace tools', async () => {
    const s = await send('who signs off a hot work permit');
    expect(s.trace.every((t) => t.tool === null)).toBe(true);
    expect(s.artifacts).toHaveLength(0);
    expect(s.messages[1].content.length).toBeGreaterThan(200);
  });

  it('a second turn resets per-turn panels but keeps the conversation', async () => {
    await send('what does the SOP say about wall loss');
    const s = await send('who signs off a hot work permit');
    expect(s.messages).toHaveLength(4);       // conversation kept
    expect(s.trace).toHaveLength(1);          // trace reset for the new turn
    expect(s.artifacts).toHaveLength(1);      // artifacts accumulate across turns
  });
});
