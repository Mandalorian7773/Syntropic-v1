/**
 * Sample panel content. Owner: person 1.
 *
 * The instrument column reads "No routing decision yet / No steps yet / No
 * files produced yet" until someone types, which is truthful and useless: a
 * judge walking past a cold machine sees three grey boxes and learns nothing
 * about what the system shows when it works.
 *
 * So the panels render this fixture while idle and untouched. It is a
 * PRESENTATION fallback, never store state -- nothing here can mix into a real
 * run's data, and the first token of a real turn replaces it wholesale. Every
 * panel showing it carries a `sample` tag, because an instrument that cannot
 * be told apart from the real reading is worse than an empty one.
 *
 * The values are the document scenario, chosen to match the mock's own
 * "...document..." path so the sample and the first real run tell one story.
 */
import type { Artifact, RouterDecision } from './types/events';
import type { TraceStep } from './store/session';

export const demoRouter: RouterDecision = {
  type: 'router.decision',
  model_id: 'qwen2.5-vl-7b',
  task_type: 'document',
  confidence: 0.87,
  reason:
    'Prompt references a scanned SOP page and asks for a figure from it. '
    + 'Vision-capable model required; text-only models cannot read the table.',
  alternatives: ['llama3.1-8b', 'qwen2.5-coder-7b'],
};

export const demoActiveModel = 'qwen2.5-vl-7b';
export const demoVramMb = 5600;
export const demoStep = 4;
export const demoMaxSteps = 10;

/**
 * Four steps: retrieval, an OCR pass, a failure, and its recovery. The failed
 * step is deliberate -- a trace where everything succeeds does not show that
 * the panel distinguishes ok from fail, which is half of what it is for.
 */
export const demoTrace: TraceStep[] = [
  {
    step: 1,
    callId: 'demo-1',
    tool: 'search_documents',
    args: { query: 'maximum permissible wall loss', top_k: 5 },
    ok: true,
    summary:
      '5 chunks from 2 documents. Top hit: SOP-114 "Pressure Vessel '
      + 'Inspection", §4.2, score 0.91.',
    durationMs: 820,
    truncated: false,
    startedAt: 0,
  },
  {
    step: 2,
    callId: 'demo-2',
    tool: 'read_page_image',
    args: { document: 'SOP-114.pdf', page: 12 },
    ok: true,
    summary:
      'Page 12 is a scanned table. Read: nominal 12.7 mm, minimum 9.5 mm, '
      + 'maximum permissible wall loss 25%.',
    durationMs: 3140,
    truncated: false,
    startedAt: 0,
  },
  {
    step: 3,
    callId: 'demo-3',
    tool: 'execute_python',
    args: { code: 'import pandas as pd\ndf = pd.read_csv("readings.csv")\n' },
    ok: false,
    summary: 'TOOL_TIMEOUT: sandbox exceeded 30s wall clock. Retrying with a narrower query.',
    durationMs: 30000,
    truncated: false,
    startedAt: 0,
  },
  {
    step: 4,
    callId: 'demo-4',
    tool: 'write_docx',
    args: { filename: 'wall-loss-summary.docx', sections: 3 },
    ok: true,
    summary: 'Wrote 3 sections, 2 tables, 4 citations. 18.4 KB.',
    durationMs: 1260,
    truncated: false,
    startedAt: 0,
  },
];

export const demoArtifacts: Artifact[] = [
  {
    type: 'artifact',
    artifact_id: 'demo-a1',
    filename: 'wall-loss-summary.docx',
    mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    size_bytes: 18841,
    url: '',
  },
  {
    type: 'artifact',
    artifact_id: 'demo-a2',
    filename: 'thickness-readings.xlsx',
    mime: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    size_bytes: 7420,
    url: '',
  },
  {
    type: 'artifact',
    artifact_id: 'demo-a3',
    filename: 'sop-114-page-12.png',
    mime: 'image/png',
    size_bytes: 244310,
    url: '',
  },
];
