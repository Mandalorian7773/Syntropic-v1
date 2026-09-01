/* ============================================================
 * GENERATED FILE -- DO NOT EDIT BY HAND.
 *
 * Source:    contracts/contracts/{events,api}.py
 * Regenerate: make types
 *
 * Hand edits are overwritten on the next 'make types' and, worse,
 * they hide contract drift that the build is supposed to catch.
 * If a type here is wrong, fix the Pydantic model and regenerate.
 * See contracts/CHANGE-PROTOCOL.md.
 * ============================================================ */

export type TaskType = 'general' | 'code' | 'document' | 'vision' | 'data';
export type StopReason = 'final_answer' | 'max_steps' | 'error' | 'cancelled';
export type Event =
  | SessionStart
  | RouterDecision
  | ModelLoading
  | ModelReady
  | AgentStep
  | Token
  | ToolCall
  | ToolResultEvent
  | Citation
  | Artifact
  | AgentError
  | Done;

export interface Contracts {
  TaskType: TaskType;
  StopReason: StopReason;
  SessionStart: SessionStart;
  RouterDecision: RouterDecision;
  ModelLoading: ModelLoading;
  ModelReady: ModelReady;
  AgentStep: AgentStep;
  Token: Token;
  ToolCall: ToolCall;
  ToolResultEvent: ToolResultEvent;
  Citation: Citation;
  Artifact: Artifact;
  AgentError: AgentError;
  Done: Done;
  Event: Event;
  EventEnvelope: EventEnvelope;
  Attachment: Attachment;
  ChatRequest: ChatRequest;
  CancelRequest: CancelRequest;
  CancelResponse: CancelResponse;
  ModelInfo: ModelInfo;
  SessionSummary: SessionSummary;
  Message: Message;
  SessionStep: SessionStep;
  SessionDetail: SessionDetail;
  UploadResponse: UploadResponse;
  DocumentInfo: DocumentInfo;
  ReindexResponse: ReindexResponse;
  SearchRequest: SearchRequest;
  SearchHit: SearchHit;
  SearchResponse: SearchResponse;
  ArtifactInfo: ArtifactInfo;
  NetworkStatus: NetworkStatus;
  HealthResponse: HealthResponse;
}
export interface SessionStart {
  type: 'session.start';
  session_id: string;
  ts: number;
}
export interface RouterDecision {
  type: 'router.decision';
  model_id: string;
  task_type: 'general' | 'code' | 'document' | 'vision' | 'data';
  confidence: number;
  reason: string;
  alternatives: string[];
}
export interface ModelLoading {
  type: 'model.loading';
  model_id: string;
  evicting: string | null;
  eta_s: number;
}
export interface ModelReady {
  type: 'model.ready';
  model_id: string;
  load_ms: number;
  vram_mb: number;
}
export interface AgentStep {
  type: 'agent.step';
  step: number;
  max_steps: number;
}
export interface Token {
  type: 'token';
  text: string;
}
export interface ToolCall {
  type: 'tool.call';
  call_id: string;
  name: string;
  args: {
    [k: string]: unknown;
  };
}
export interface ToolResultEvent {
  type: 'tool.result';
  call_id: string;
  ok: boolean;
  summary: string;
  duration_ms: number;
  truncated: boolean;
}
export interface Citation {
  type: 'citation';
  doc_id: string;
  filename: string;
  page: number;
  score: number;
  snippet: string;
}
export interface Artifact {
  type: 'artifact';
  artifact_id: string;
  filename: string;
  mime: string;
  size_bytes: number;
  url: string;
}
export interface AgentError {
  type: 'error';
  code: string;
  message: string;
  recoverable: boolean;
}
export interface Done {
  type: 'done';
  stop_reason: 'final_answer' | 'max_steps' | 'error' | 'cancelled';
  steps_used: number;
  tokens_in: number;
  tokens_out: number;
  latency_ms: number;
}
/**
 * Wrapper that exists only so the union gets a name in the JSON Schema.
 *
 * The frontend consumes `Event` from the generated events.ts; this envelope is
 * what makes the discriminated union addressable by json-schema-to-typescript.
 */
export interface EventEnvelope {
  event:
    | SessionStart
    | RouterDecision
    | ModelLoading
    | ModelReady
    | AgentStep
    | Token
    | ToolCall
    | ToolResultEvent
    | Citation
    | Artifact
    | AgentError
    | Done;
}
export interface Attachment {
  filename: string;
  mime: string;
  size_bytes: number;
  path: string | null;
}
export interface ChatRequest {
  session_id: string | null;
  message: string;
  attachments: Attachment[];
}
export interface CancelRequest {
  session_id: string;
}
export interface CancelResponse {
  ok: boolean;
}
export interface ModelInfo {
  id: string;
  capabilities: string[];
  context: number;
  vram_mb: number;
  loaded: boolean;
}
/**
 * One row of GET /api/sessions (bare array of these).
 */
export interface SessionSummary {
  id: string;
  title: string;
  created_at: number;
  message_count: number;
}
export interface Message {
  role: string;
  content: string;
  ts: number;
}
/**
 * A replayed agent step, for rehydrating the trace panel on session load.
 */
export interface SessionStep {
  step: number;
  tool: string;
  args: {
    [k: string]: unknown;
  };
  ok: boolean;
  summary: string;
  duration_ms: number;
}
/**
 * GET /api/sessions/{id}.
 */
export interface SessionDetail {
  id: string;
  messages: Message[];
  steps: SessionStep[];
}
/**
 * POST /api/documents/upload (multipart).
 */
export interface UploadResponse {
  file_id: string;
  filename: string;
  pages: number;
  status: string;
}
/**
 * One row of GET /api/documents (bare array of these).
 */
export interface DocumentInfo {
  doc_id: string;
  filename: string;
  pages: number;
  chunks: number;
  ingested_at: number;
  status: string;
  size_bytes: number;
}
export interface ReindexResponse {
  doc_id: string;
  queued: boolean;
}
export interface SearchRequest {
  query: string;
  top_k: number;
}
export interface SearchHit {
  doc_id: string;
  filename: string;
  page: number;
  score: number;
  snippet: string;
}
export interface SearchResponse {
  hits: SearchHit[];
}
export interface ArtifactInfo {
  artifact_id: string;
  filename: string;
  mime: string;
  size_bytes: number;
  url: string;
}
export interface NetworkStatus {
  external_packets: number;
  dns_queries: number;
  since: number;
  rules_active: boolean;
}
export interface HealthResponse {
  ok: boolean;
  model_loaded: string | null;
  qdrant: boolean;
  vram_free_mb: number;
}
