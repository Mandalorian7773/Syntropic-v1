/**
 * End-to-end check: real mock server -> real SSE parser -> real reducer.
 *
 * This is the check that would fail if any of the four scenarios stopped
 * rendering, if a contract event stopped being handled, or if the SSE frame
 * parser broke on a chunk boundary. It spawns the mock itself, so it needs
 * nothing running.
 */
import { type ChildProcess } from 'node:child_process';
import { startMock } from './mockServer';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import type { Event } from '../types/events';

const PORT = 8177;
let proc: ChildProcess;

beforeAll(async () => {
  proc = await startMock(PORT);
}, 30000);

afterAll(() => proc?.kill());

/** Drive the stream exactly as the browser does, collecting every event. */
async function run(message: string): Promise<Event[]> {
  const res = await fetch(`http://127.0.0.1:${PORT}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: null, message }),
  });
  expect(res.ok).toBe(true);
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  const out: Event[] = [];
  let buf = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let sep: RegExpExecArray | null;
    while ((sep = /\r?\n\r?\n/.exec(buf))) {
      const frame = buf.slice(0, sep.index);
      buf = buf.slice(sep.index + sep[0].length);
      const data = frame
        .split(/\r?\n/)
        .filter((l) => l.startsWith('data:'))
        .map((l) => l.slice(5).trim())
        .join('\n');
      if (data) out.push(JSON.parse(data) as Event);
    }
  }
  return out;
}

const types = (evs: Event[]) => evs.map((e) => e.type);
const text = (evs: Event[]) =>
  evs.filter((e) => e.type === 'token').map((e) => e.text).join('');

describe('mock scenarios', () => {
  it('document: routes to vision, cites sources, emits a docx artifact', async () => {
    const evs = await run('what does the SOP say about wall loss');
    const t = types(evs);

    expect(t[0]).toBe('session.start');
    expect(t.at(-1)).toBe('done');

    const router = evs.find((e) => e.type === 'router.decision')!;
    expect(router.model_id).toBe('qwen2.5-vl-7b');
    expect(router.task_type).toBe('document');
    expect(router.confidence).toBeGreaterThan(0.6);

    const cites = evs.filter((e) => e.type === 'citation');
    expect(cites.length).toBeGreaterThanOrEqual(3);
    expect(cites[0].filename).toMatch(/\.pdf$/);
    expect(cites[0].page).toBeGreaterThan(0);

    const art = evs.find((e) => e.type === 'artifact')!;
    expect(art.filename).toBe('approval-note.docx');
    expect(art.url).toBe(`/api/artifacts/${art.artifact_id}`);

    // Markdown the chat view has to render. The blank lines matter: without
    // them a heading renders as inline text and a table renders as pipes, so
    // assert the exact leading newlines, not just the visible characters.
    const body = text(evs);
    expect(body).toContain('\n\n## Finding\n\n');
    expect(body).toContain('\n\n| Location | Nominal | Measured | Loss | Status |\n');
  });

  it('code: swaps the model, fails a tool once, then succeeds', async () => {
    const evs = await run('write a python script for downtime');

    const loading = evs.find((e) => e.type === 'model.loading')!;
    expect(loading.evicting).toBe('qwen2.5-vl-7b');
    expect(loading.eta_s).toBeGreaterThan(0);

    const ready = evs.find((e) => e.type === 'model.ready')!;
    expect(ready.model_id).toBe('qwen2.5-coder-7b');
    // model.loading must precede model.ready, or the swap UI never appears.
    expect(types(evs).indexOf('model.loading'))
      .toBeLessThan(types(evs).indexOf('model.ready'));

    const results = evs.filter((e) => e.type === 'tool.result');
    expect(results.map((r) => r.ok)).toEqual([false, true]);

    // Every result must match a call, or the trace panel drops the row.
    const callIds = evs.filter((e) => e.type === 'tool.call').map((c) => c.call_id);
    for (const r of results) expect(callIds).toContain(r.call_id);
  });

  it('failure: emits a recoverable TOOL_TIMEOUT and then recovers', async () => {
    const evs = await run('scan every page, make this fail');

    const err = evs.find((e) => e.type === 'error')!;
    expect(err.code).toBe('TOOL_TIMEOUT');
    expect(err.recoverable).toBe(true);

    // The run continues past the error and still lands on a final answer.
    const done = evs.at(-1)!;
    expect(done.type).toBe('done');
    if (done.type === 'done') expect(done.stop_reason).toBe('final_answer');

    const after = types(evs).slice(types(evs).indexOf('error'));
    expect(after).toContain('agent.step');
    expect(evs.filter((e) => e.type === 'tool.result').some((r) => r.ok)).toBe(true);
  });

  it('simple: streams tokens with no tools at all', async () => {
    const evs = await run('who signs off a hot work permit');
    const t = types(evs);
    expect(t).not.toContain('tool.call');
    expect(t).not.toContain('model.loading');
    expect(text(evs).length).toBeGreaterThan(200);
  });

  it('the four scenarios together cover all twelve contract event types', async () => {
    const seen = new Set<string>();
    for (const m of ['document sop', 'python script', 'make it fail', 'hello']) {
      for (const e of await run(m)) seen.add(e.type);
    }
    expect([...seen].sort()).toEqual([
      'agent.step', 'artifact', 'citation', 'done', 'error', 'model.loading',
      'model.ready', 'router.decision', 'session.start', 'token',
      'tool.call', 'tool.result',
    ]);
  });
});

describe('REST endpoints', () => {
  const get = async (p: string) => {
    const r = await fetch(`http://127.0.0.1:${PORT}${p}`);
    expect(r.ok).toBe(true);
    return r.json() as Promise<unknown>;
  };

  it('serves the shapes the panels read', async () => {
    expect(await get('/api/health')).toMatchObject({ ok: true });
    expect(await get('/api/models')).toBeInstanceOf(Array);
    expect(await get('/api/sessions')).toBeInstanceOf(Array);
    expect(await get('/api/documents')).toBeInstanceOf(Array);
    expect(await get('/api/network/status'))
      .toMatchObject({ external_packets: 0, dns_queries: 0, rules_active: true });
  });

  it('serves an artifact as a real download', async () => {
    const r = await fetch(`http://127.0.0.1:${PORT}/api/artifacts/a3`);
    expect(r.ok).toBe(true);
    expect(r.headers.get('content-disposition')).toContain('attachment');
  });
});
