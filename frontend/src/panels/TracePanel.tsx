/**
 * Agent trace. Owner: person 1.
 *
 * Steps stream in live. Each row: tool name, args (collapsed by default),
 * result summary, ok/failed, duration. A running step N of max_steps counter
 * sits in the header.
 *
 * A step that is still running shows a live-ticking duration rather than a
 * spinner, because "1.8s and counting" tells you the machine is working and a
 * spinner does not.
 */
import { useEffect, useState } from 'react';
import { useSession } from '../store/session';
import type { TraceStep } from '../store/session';
import { Empty, Panel, ms } from '../components/ui';

export default function TracePanel({ className = '' }: { className?: string }) {
  const trace = useSession((s) => s.trace);
  const step = useSession((s) => s.step);
  const maxSteps = useSession((s) => s.maxSteps);
  const errors = useSession((s) => s.errors);

  return (
    <Panel
      title="Agent trace"
      className={className}
      right={
        maxSteps > 0 ? (
          <span className="font-mono text-tiny tabular-nums text-steel-400">
            step <span className="text-steel-100">{step}</span>
            <span className="text-steel-600"> / {maxSteps}</span>
          </span>
        ) : null
      }
    >
      {trace.length === 0 ? (
        <Empty>No steps yet.</Empty>
      ) : (
        <ol className="divide-y divide-steel-850">
          {trace.map((t, i) => (
            <Step key={`${t.step}-${t.callId ?? i}`} step={t} index={i} />
          ))}
        </ol>
      )}

      {errors.length > 0 && (
        <div className="border-t border-fault-dim">
          {errors.map((e, i) => (
            <div key={i} className="bg-fault-deep/40 px-3 py-1.5">
              <div className="flex items-baseline justify-between gap-2">
                <span className="font-mono text-tiny font-semibold text-fault">
                  {e.code}
                </span>
                <span className="label shrink-0">
                  {e.recoverable ? 'recoverable' : 'fatal'}
                </span>
              </div>
              <p className="font-mono text-tiny text-steel-300">{e.message}</p>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function Step({ step, index }: { step: TraceStep; index: number }) {
  const [open, setOpen] = useState(false);
  const running = step.ok === null;
  const hasArgs = Object.keys(step.args).length > 0;

  return (
    <li className="px-3 py-2">
      <div className="flex items-baseline gap-2">
        <span
          className={`w-5 shrink-0 font-mono text-tiny tabular-nums
                      ${running ? 'text-work' : 'text-steel-600'}`}
        >
          {String(step.step || index + 1).padStart(2, '0')}
        </span>

        <button
          type="button"
          onClick={() => hasArgs && setOpen((v) => !v)}
          disabled={!hasArgs}
          className={`min-w-0 flex-1 truncate text-left font-mono text-tiny
                      ${hasArgs ? 'cursor-pointer hover:text-accent' : ''}
                      ${running ? 'text-steel-100' : 'text-steel-200'}`}
        >
          {hasArgs && (
            <span className="mr-1 inline-block w-2 text-steel-600">
              {open ? '▾' : '▸'}
            </span>
          )}
          {step.tool ?? <span className="text-steel-500">thinking…</span>}
        </button>

        <Status step={step} />
      </div>

      {open && hasArgs && (
        <pre className="mt-1.5 max-h-56 overflow-auto scroll-thin rounded-sm
                        border border-steel-800 bg-steel-950 p-2 font-mono
                        text-tiny leading-relaxed text-steel-400">
          {JSON.stringify(step.args, null, 2)}
        </pre>
      )}

      {step.summary && <Summary step={step} />}
    </li>
  );
}

/** How much of a tool result to show before it needs asking for. */
const SUMMARY_PREVIEW_CHARS = 220;

/**
 * A tool result, clamped until asked for.
 *
 * search_documents returns up to a thousand tokens of JSON -- that is its
 * contract, and it is the right amount to hand a model. Rendered raw it fills
 * the trace panel and pushes every other step off screen, so the one column
 * that exists to show what the agent did stops showing it. Collapsed by
 * default, whole on request, and never silently cut: the preview says how much
 * more there is.
 */
function Summary({ step }: { step: TraceStep }) {
  const [open, setOpen] = useState(false);
  const text = step.summary ?? '';
  const long = text.length > SUMMARY_PREVIEW_CHARS || text.split('\n').length > 4;
  const shown = open || !long ? text : `${text.slice(0, SUMMARY_PREVIEW_CHARS).trimEnd()}…`;

  return (
    <div className="mt-1 pl-7">
      <p
        className={`whitespace-pre-wrap break-words font-mono text-tiny
                    leading-relaxed ${step.ok ? 'text-steel-400' : 'text-fault'}
                    ${open ? 'max-h-64 overflow-auto scroll-thin' : ''}`}
      >
        {shown}
        {step.truncated && !long && (
          <span className="ml-1 text-steel-600">[truncated]</span>
        )}
      </p>
      {long && (
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="mt-0.5 font-mono text-micro text-steel-500
                     hover:text-accent"
        >
          {open
            ? '▾ show less'
            : `▸ show all ${text.length.toLocaleString()} characters`}
          {step.truncated && (
            <span className="ml-1 text-steel-600">
              (tool output was truncated at the source)
            </span>
          )}
        </button>
      )}
    </div>
  );
}

function Status({ step }: { step: TraceStep }) {
  // Still running: no verdict and no frozen duration yet.
  if (step.ok === null && step.durationMs === null) {
    return <LiveDuration startedAt={step.startedAt} />;
  }
  // Frozen but never resolved -- the run ended under it.
  if (step.ok === null) {
    return (
      <span className="shrink-0 font-mono text-tiny tabular-nums text-steel-500">
        {step.durationMs !== null ? ms(step.durationMs) : ''}
        <span className="ml-1.5 text-steel-600">abandoned</span>
      </span>
    );
  }
  return (
    <span className="flex shrink-0 items-baseline gap-1.5 font-mono text-tiny
                     tabular-nums">
      <span className="text-steel-500">
        {step.durationMs !== null ? ms(step.durationMs) : ''}
      </span>
      <span className={step.ok ? 'text-iso' : 'text-fault'}>
        {step.ok ? 'ok' : 'fail'}
      </span>
    </span>
  );
}

/** Counts up while a tool runs. Silence looks like a crash; a moving number does not. */
function LiveDuration({ startedAt }: { startedAt: number }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 100);
    return () => clearInterval(id);
  }, []);
  return (
    <span className="shrink-0 font-mono text-tiny tabular-nums text-work">
      {((now - startedAt) / 1000).toFixed(1)}s
      <span className="ml-1 animate-pulse-slow">●</span>
    </span>
  );
}
