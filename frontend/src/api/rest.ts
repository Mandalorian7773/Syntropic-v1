/**
 * REST client. Owner: person 1.
 *
 * Every response type comes from the GENERATED types/events.ts. Nothing is
 * hand-typed here, so a contract change breaks this file at build time.
 *
 * API_BASE is the single switch between the mock server and the real backend.
 * Set VITE_API_BASE to point elsewhere; by default everything is same-origin
 * and the vite proxy (dev) or nginx (demo) forwards /api. That is the "only
 * edit required" when person 3's backend lands.
 */
import type {
  ArtifactInfo, CancelResponse, DocumentInfo, HealthResponse, ModelInfo,
  NetworkStatus, SessionDetail, SessionSummary, UploadResponse,
} from '../types/events';

export const API_BASE: string = import.meta.env.VITE_API_BASE ?? '';

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`GET ${path} -> ${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`POST ${path} -> ${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

export const health = () => get<HealthResponse>('/api/health');
export const models = () => get<ModelInfo[]>('/api/models');
export const sessions = () => get<SessionSummary[]>('/api/sessions');
export const session = (id: string) => get<SessionDetail>(`/api/sessions/${id}`);
export const documents = () => get<DocumentInfo[]>('/api/documents');
export const networkStatus = () => get<NetworkStatus>('/api/network/status');

export const cancelChat = (sessionId: string) =>
  post<CancelResponse>('/api/chat/cancel', { session_id: sessionId });

export const reindex = (docId: string) =>
  post<{ doc_id: string; queued: boolean }>(`/api/documents/${docId}/reindex`);

export async function uploadDocument(file: File): Promise<UploadResponse> {
  const form = new FormData();
  form.append('file', file);
  const res = await fetch(`${API_BASE}/api/documents/upload`, {
    method: 'POST',
    body: form,
  });
  if (!res.ok) throw new Error(`upload failed -> ${res.status} ${res.statusText}`);
  return (await res.json()) as UploadResponse;
}

/** Artifacts are served as a file download, so this is a URL, not a fetch. */
export const artifactUrl = (a: Pick<ArtifactInfo, 'url'>) =>
  `${API_BASE}${a.url}`;
