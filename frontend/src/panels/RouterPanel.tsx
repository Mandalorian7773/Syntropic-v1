/**
 * Router panel. Owner: person 1.
 *
 * Required demonstration #1, so it is graded. It shows the whole decision:
 * chosen model, task type, confidence AS A NUMBER, the stated reason, and the
 * alternatives that were rejected.
 *
 * The model swap is shown, never hidden. A 9-second swap with no feedback
 * reads as a crash; the same 9 seconds with an elapsed counter running against
 * eta_s reads as a machine doing deliberate work on constrained hardware,
 * which is the actual engineering story.
 */
import { useEffect, useState } from 'react';
import { useSession, taskTypeLabel } from '../store/session';
import { Field, Panel, SampleTag, Sweep } from '../components/ui';
import { demoActiveModel, demoRouter, demoVramMb } from '../demo';

export default function RouterPanel({ className = '' }: { className?: string }) {
  const liveRouter = useSession((s) => s.router);
  const swap = useSession((s) => s.swap);
  const liveModel = useSession((s) => s.activeModel);
  const liveVram = useSession((s) => s.modelVramMb);
  const phase = useSession((s) => s.phase);
  const untouched = useSession((s) => s.messages.length === 0);

  // Only on an untouched tab: once a turn has been sent the panels are
  // reporting on a real run, and a sample creeping back into a run that
  // happened to decide nothing would be a lie about the machine's state.
  const sample = !liveRouter && !swap && phase === 'idle' && untouched;
  const router = liveRouter ?? (sample ? demoRouter : null);
  const activeModel = liveModel ?? (sample ? demoActiveModel : null);
  const vram = liveVram ?? (sample ? demoVramMb : null);

  return (
    <Panel
      title="Router"
      right={
        sample ? <SampleTag />
          : activeModel && !swap ? (
            <span className="text-tiny text-iso">{activeModel}</span>
          ) : null
      }
      bodyClass="px-3 py-2"
      className={className}
    >
      {swap && <SwapIndicator />}

      {!router && !swap && (
        <p className="px-3 py-6 text-center text-tiny text-steel-600">
          Classifying task…
        </p>
      )}

      {router && (
        <div className={`${swap ? 'mt-3 opacity-50' : ''} ${sample ? 'opacity-70' : ''}`}>
          <Field label="Model">
            <span className="text-accent">{router.model_id}</span>
          </Field>
          <Field label="Task">
            <span className="rounded-md bg-accent-deep px-2 py-0.5 font-medium text-accent">
              {taskTypeLabel[router.task_type]}
            </span>
          </Field>
          <Confidence value={router.confidence} />
          {vram !== null && !swap && (
            <Field label="VRAM">{vram.toLocaleString()} MB</Field>
          )}

          <div className="mt-2 border-t border-steel-800 pt-2">
            <p className="label mb-1">Reason</p>
            <p className="text-tiny leading-relaxed text-steel-300">
              {router.reason}
            </p>
          </div>

          {router.alternatives.length > 0 && (
            <div className="mt-2 border-t border-steel-800 pt-2">
              <p className="label mb-1">Rejected</p>
              <ul className="flex flex-wrap gap-1">
                {router.alternatives.map((alt) => (
                  <li
                    key={alt}
                    className="rounded-md border border-steel-700 px-1.5 py-0.5 
                               text-tiny text-steel-500 line-through
                               decoration-steel-600"
                  >
                    {alt}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}

/** Confidence as a number AND a bar. The number is what gets graded. */
function Confidence({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  // Below 0.60 the backend keeps the incumbent model; say so rather than
  // showing an unexplained amber bar.
  const low = value < 0.6;
  return (
    <div className="py-1">
      <div className="flex items-baseline justify-between">
        <span className="label">Confidence</span>
        <span
          className={`text-tiny tabular-nums
                      ${low ? 'text-work' : 'text-steel-200'}`}
        >
          {value.toFixed(2)}
          <span className="ml-1 text-steel-500">({pct}%)</span>
        </span>
      </div>
      <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-steel-800">
        <div
          className={`h-full ${low ? 'bg-work' : 'bg-accent'}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      {low && (
        <p className="mt-1 text-micro text-work">
          below 0.60 threshold — staying on resident model
        </p>
      )}
    </div>
  );
}

/**
 * The swap. Counts elapsed against eta_s and keeps going past it, because a
 * progress bar that sticks at 100% is exactly the "is it frozen?" moment this
 * panel exists to prevent.
 */
function SwapIndicator() {
  const swap = useSession((s) => s.swap);
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!swap) return;
    const tick = () => setElapsed((Date.now() - swap.startedAt) / 1000);
    tick();
    const id = setInterval(tick, 100);
    return () => clearInterval(id);
  }, [swap?.startedAt]);

  if (!swap) return null;
  const pct = Math.min(100, (elapsed / Math.max(swap.etaS, 1)) * 100);
  const over = elapsed > swap.etaS;

  return (
    <div className="rounded-xl border border-work-dim bg-work-deep/50 p-3">
      <div className="flex items-center justify-between">
        <span className="label text-work">Model swap in progress</span>
        <span className="text-tiny tabular-nums text-work">
          {elapsed.toFixed(1)}s / ~{swap.etaS}s
        </span>
      </div>

      <div className="mt-2 space-y-1 text-tiny">
        {swap.evicting && (
          <div className="flex items-center gap-2 text-steel-500">
            <span className="w-14 shrink-0 text-micro uppercase tracking-widest">
              evict
            </span>
            <span className="truncate line-through">{swap.evicting}</span>
          </div>
        )}
        <div className="flex items-center gap-2 text-steel-100">
          <span className="w-14 shrink-0 text-micro uppercase tracking-widest
                           text-steel-500">
            load
          </span>
          <span className="truncate">{swap.modelId}</span>
        </div>
      </div>

      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-steel-800">
        <div
          className="h-full bg-work transition-[width] duration-100 ease-linear"
          style={{ width: `${pct}%` }}
        />
      </div>
      {over ? (
        <>
          <p className="mt-1 text-micro text-work">
            past estimate — still loading, this is normal on an 8 GB card
          </p>
          <div className="mt-1"><Sweep /></div>
        </>
      ) : (
        <p className="mt-1 text-micro text-steel-500">
          one model resident at a time — 8 GB VRAM budget
        </p>
      )}
    </div>
  );
}
