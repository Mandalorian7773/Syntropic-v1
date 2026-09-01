/**
 * Typed REST client. Owner: person 1.
 *
 * Stub: one generic helper. Person 1 adds the concrete calls (models,
 * sessions, documents, search, artifacts, network status) as the panels that
 * need them get built. Request and response types come from the GENERATED
 * types/events.ts -- never hand-written here.
 */
import type { HealthResponse } from '../types/events';

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return (await res.json()) as T;
}

export const health = () => get<HealthResponse>('/api/health');
