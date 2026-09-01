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
  ModelsResponse: ModelsResponse;
  SessionSummary: SessionSummary;
  SessionsResponse: SessionsResponse;
  Message: Message;
  SessionDetail: SessionDetail;
  DocumentInfo: DocumentInfo;
  UploadResponse: UploadResponse;
  DocumentsResponse: DocumentsResponse;
  ReindexResponse: ReindexResponse;
  SearchRequest: SearchRequest;
  SearchHit: SearchHit;
  SearchResponse: SearchResponse;
  ArtifactInfo: ArtifactInfo;
  NetworkStatus: NetworkStatus;
  HealthResponse: HealthResponse;
}
export interface SessionStart {
  type?: 'session.start';
  session_id: string;
  ts: number;
}
export interface RouterDecision {
  type?: 'router.decision';
  model_id: string;
  task_type: 'general' | 'code' | 'document' | 'vision' | 'data';
  confidence: number;
  reason: string;
  alternatives?: string[];
}
export interface ModelLoading {
  type?: 'model.loading';
  model_id: string;
  evicting?: string | null;
  eta_s: number;
}
export interface ModelReady {
  type?: 'model.ready';
  model_id: string;
  load_ms: number;
  vram_mb: number;
}
export interface AgentStep {
  type?: 'agent.step';
  step: number;
  max_steps: number;
}
export interface Token {
  type?: 'token';
  text: string;
}
export interface ToolCall {
  type?: 'tool.call';
  call_id: string;
  name: string;
  args?: {
    [k: string]: unknown;
  };
}
export interface ToolResultEvent {
  type?: 'tool.result';
  call_id: string;
  ok: boolean;
  summary: string;
  duration_ms: number;
  truncated?: boolean;
}
export interface Citation {
  type?: 'citation';
  doc_id: string;
  filename: string;
  page: number;
  score: number;
  snippet: string;
}
export interface Artifact {
  type?: 'artifact';
  artifact_id: string;
  filename: string;
  mime: string;
  size_bytes: number;
  url: string;
}
export interface AgentError {
  type?: 'error';
  code: string;
  message: string;
  recoverable: boolean;
}
export interface Done {
  type?: 'done';
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
  path?: string | null;
}
export interface ChatRequest {
  session_id?: string | null;
  message: string;
  attachments?: Attachment[];
}
export interface CancelRequest {
  session_id: string;
}
export interface CancelResponse {
  cancelled: boolean;
}
export interface ModelInfo {
  id: string;
  capabilities?: string[];
  context: number;
  vram_mb: number;
  loaded: boolean;
}
export interface ModelsResponse {
  models?: ModelInfo[];
}
export interface SessionSummary {
  session_id: string;
  title: string;
  created_ts: number;
  updated_ts: number;
  message_count: number;
}
export interface SessionsResponse {
  sessions?: SessionSummary[];
}
export interface Message {
  role: string;
  content: string;
  ts: number;
}
export interface SessionDetail {
  session_id: string;
  title: string;
  created_ts: number;
  updated_ts: number;
  task_type?: ('general' | 'code' | 'document' | 'vision' | 'data') | null;
  messages?: Message[];
}
export interface DocumentInfo {
  id: string;
  filename: string;
  pages: number;
  chunks: number;
  size_bytes: number;
  indexed: boolean;
  ingested_ts: number;
}
export interface UploadResponse {
  document: DocumentInfo;
}
export interface DocumentsResponse {
  documents?: DocumentInfo[];
}
export interface ReindexResponse {
  id: string;
  queued: boolean;
}
export interface SearchRequest {
  query: string;
  top_k?: number;
}
export interface SearchHit {
  doc_id: string;
  filename: string;
  page: number;
  score: number;
  snippet: string;
}
export interface SearchResponse {
  hits?: SearchHit[];
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
  model_loaded?: string | null;
  qdrant: boolean;
  vram_free_mb: number;
}
