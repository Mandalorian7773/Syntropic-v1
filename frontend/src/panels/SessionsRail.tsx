/**
 * Left rail: view switcher, session list, health readout. Owner: person 1.
 */
import { useEffect, useState } from 'react';
import type { HealthResponse, SessionSummary } from '../types/events';
import { health, sessions } from '../api/rest';
import { useSession } from '../store/session';
import { Dot } from '../components/ui';

export type View = 'chat' | 'documents' | 'benchmark';

const VIEWS: { id: View; label: string }[] = [
  { id: 'chat', label: 'Workbench' },
  { id: 'documents', label: 'Documents' },
  { id: 'benchmark', label: 'Benchmarks' },
];

export default function SessionsRail({ view, onView }: {
  view: View; onView: (v: View) => void;
}) {
  const [list, setList] = useState<SessionSummary[]>([]);
  const [hp, setHp] = useState<HealthResponse | null>(null);
  const clear = useSession((s) => s.clear);
  const activeId = useSession((s) => s.sessionId);

  useEffect(() => {
    void sessions().then(setList).catch(() => setList([]));
    const poll = () => void health().then(setHp).catch(() => setHp(null));
    poll();
    const id = setInterval(poll, 5000);
    return () => clearInterval(id);
  }, []);

  return (
    <nav className="flex w-56 shrink-0 flex-col border-r border-steel-800
                    bg-steel-900">
      <div className="border-b border-steel-800 px-3 py-3">
        <p className="font-mono text-sm font-semibold tracking-tight text-steel-100">
          SIH<span className="text-accent">26117</span>
        </p>
        <p className="label mt-0.5">Sovereign AI workbench</p>
      </div>

      <ul className="border-b border-steel-800 py-1">
        {VIEWS.map((v) => (
          <li key={v.id}>
            <button
              type="button"
              onClick={() => onView(v.id)}
              className={`w-full px-3 py-1.5 text-left font-mono text-tiny
                          ${view === v.id
                            ? 'border-l-2 border-accent bg-steel-850 text-accent'
                            : 'border-l-2 border-transparent text-steel-400 hover:bg-steel-850 hover:text-steel-200'}`}
            >
              {v.label}
            </button>
          </li>
        ))}
      </ul>

      <div className="flex items-center justify-between px-3 pb-1 pt-3">
        <span className="label">Sessions</span>
        <button
          type="button"
          onClick={clear}
          className="font-mono text-micro uppercase tracking-widest text-steel-500
                     hover:text-accent"
        >
          + new
        </button>
      </div>

      <ul className="min-h-0 flex-1 overflow-y-auto scroll-thin px-1.5">
        {list.length === 0 && (
          <li className="px-1.5 py-2 font-mono text-micro text-steel-600">
            No stored sessions.
          </li>
        )}
        {list.map((s) => (
          <li key={s.id}>
            <div
              className={`border-l-2 px-2 py-1.5 ${
                s.id === activeId
                  ? 'border-accent bg-steel-850'
                  : 'border-transparent hover:bg-steel-850'}`}
            >
              <p className="truncate text-tiny text-steel-300" title={s.title}>
                {s.title}
              </p>
              <p className="font-mono text-micro text-steel-600">
                {new Date(s.created_at * 1000).toLocaleDateString()} ·{' '}
                {s.message_count} msg
              </p>
            </div>
          </li>
        ))}
      </ul>

      <div className="space-y-1 border-t border-steel-800 px-3 py-2">
        <Health label="Backend" ok={hp?.ok ?? false}
                value={hp ? 'up' : 'unreachable'} />
        <Health label="Qdrant" ok={hp?.qdrant ?? false}
                value={hp?.qdrant ? 'up' : 'down'} />
        <Health label="Model" ok={Boolean(hp?.model_loaded)}
                value={hp?.model_loaded ?? 'none'} />
        <Health label="VRAM free" ok={(hp?.vram_free_mb ?? 0) > 0}
                value={hp ? `${hp.vram_free_mb.toLocaleString()} MB` : '—'} />
      </div>
    </nav>
  );
}

function Health({ label, ok, value }: {
  label: string; ok: boolean; value: string;
}) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="label">{label}</span>
      <span className="flex min-w-0 items-center gap-1.5">
        <Dot tone={ok ? 'iso' : 'fault'} />
        <span className="truncate font-mono text-micro text-steel-400"
              title={value}>
          {value}
        </span>
      </span>
    </div>
  );
}
