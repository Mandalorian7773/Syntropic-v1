/**
 * The model picker, end to end against the mock: store -> SSE client -> mock.
 *
 * No backend and no GPU. The mock refuses an unknown or incapable model on the
 * same two grounds the gateway does, so the picker's error state is exercised
 * here rather than discovered on the demo hardware.
 */
import { type ChildProcess } from 'node:child_process';
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';

import { startMock } from './mockServer';
import { useSession } from '../store/session';
import type { ModelInfo } from '../types/events';

const PORT = 8178;  // matches VITE_API_BASE in vite.config.ts
let proc: ChildProcess;

beforeAll(async () => {
  proc = await startMock(PORT);
  // The store's REST and SSE clients both read API_BASE from the environment
  // at module load, so the suite points them at this mock the same way the
  // dev server's proxy points them at a backend.
  useSession.getState().loadModels();
  await waitFor(() => useSession.getState().models.length > 0);
}, 30000);

afterAll(() => proc?.kill());

beforeEach(() => {
  useSession.getState().clear();
  useSession.getState().selectModel(null);
});

async function waitFor(pred: () => boolean, ms = 15000) {
  for (let i = 0; i < ms / 25; i++) {
    if (pred()) return;
    await new Promise((r) => setTimeout(r, 25));
  }
  throw new Error('condition never became true');
}

/** Send, then wait for the store to settle back to idle. */
async function send(message: string) {
  useSession.getState().send(message);
  await waitFor(() => {
    const s = useSession.getState();
    return s.phase === 'idle' && (s.lastRun !== null || s.errors.length > 0);
  });
  return useSession.getState();
}

const byCapability = (models: ModelInfo[], cap: string) =>
  models.find((m) => m.capabilities.includes(cap))!;
const withoutCapability = (models: ModelInfo[], cap: string) =>
  models.find((m) => !m.capabilities.includes(cap))!;

describe('/api/models feeds a usable picker', () => {
  it('carries a name, a description and a cost for every model', () => {
    const { models } = useSession.getState();
    expect(models.length).toBeGreaterThan(1);
    for (const m of models) {
      expect(m.display_name).toBeTruthy();
      expect(m.display_name).not.toBe(m.id);
      expect(m.description).toBeTruthy();
      expect(m.vram_mb).toBeGreaterThan(0);
      expect(m.context).toBeGreaterThan(0);
      expect(m.capabilities.length).toBeGreaterThan(0);
    }
  });

  it('marks exactly one model resident, because the card holds one', () => {
    const loaded = useSession.getState().models.filter((m) => m.loaded);
    expect(loaded).toHaveLength(1);
  });
});

describe('choosing a model', () => {
  it('runs the turn on the chosen model and says the user chose it', async () => {
    const coder = byCapability(useSession.getState().models, 'code');
    useSession.getState().selectModel(coder.id);

    const s = await send('write a python script to plot downtime');

    expect(s.router?.model_id).toBe(coder.id);
    expect(s.router?.confidence).toBe(1.0);
    expect(s.router?.reason).toContain('user selected');
    expect(s.activeModel).toBe(coder.id);
    expect(s.errors).toHaveLength(0);
  });

  it('reports the swap so the UI can show progress rather than silence', async () => {
    const models = useSession.getState().models;
    const resident = models.find((m) => m.loaded)!;
    const other = models.find((m) => m.id !== resident.id)!;
    // Pick something the other model can actually do, or this is a refusal test.
    const prompt = other.capabilities.includes('code')
      ? 'write a python script to plot downtime'
      : 'what does the SOP say about wall loss';

    useSession.getState().selectModel(other.id);
    const s = await send(prompt);

    expect(s.activeModel).toBe(other.id);
    expect(s.modelVramMb).toBe(other.vram_mb);
    // model.ready is the authority on what is resident; the picker follows it.
    expect(s.models.find((m) => m.loaded)?.id).toBe(other.id);
    // And the swap is cleared once it is over, so no progress bar is orphaned.
    expect(s.swap).toBeNull();
  });

  it('keeps the router in charge when nothing is picked', async () => {
    useSession.getState().selectModel(null);
    const s = await send('who signs off a hot work permit');

    expect(s.router).not.toBeNull();
    expect(s.router?.reason).not.toContain('user selected');
    expect(s.router?.confidence).toBeLessThan(1.0);
  });
});

describe('refusing a model that cannot do the job', () => {
  it('surfaces the reason instead of silently routing elsewhere', async () => {
    const models = useSession.getState().models;
    const blind = withoutCapability(models, 'document');
    useSession.getState().selectModel(blind.id);

    const s = await send('what does the SOP say about wall loss, cite the page');

    expect(s.errors.length).toBeGreaterThan(0);
    const message = s.errors[0].message;
    // The detail from the server, not "backend returned 400 Bad Request".
    expect(message).toContain(blind.id);
    expect(message).toContain('document');
    expect(message).toMatch(/Try |No configured model/);
    // Nothing pretended to answer.
    expect(s.router).toBeNull();
  });

  it('names the available models when the id is unknown', async () => {
    useSession.getState().selectModel('gpt-9-turbo');
    const s = await send('anything at all');

    expect(s.errors.length).toBeGreaterThan(0);
    const message = s.errors[0].message;
    expect(message).toContain('gpt-9-turbo');
    for (const m of useSession.getState().models) {
      expect(message).toContain(m.id);
    }
  });
});
