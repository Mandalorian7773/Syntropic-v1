/**
 * Left rail: view switcher, session list, health readout. Owner: person 1.
 */
import { useEffect, useState } from 'react';
import type { HealthResponse, SessionSummary } from '../types/events';
import { health, sessions } from '../api/rest';
import { useSession } from '../store/session';
import { Dot } from '../components/ui';
import {
  ChartIcon, ChatIcon, ChevronIcon, DocIcon, EditIcon, Logo, ShieldIcon,
} from '../components/icons';
import type { ReactNode } from 'react';

export type View = 'chat' | 'documents' | 'benchmark';

export const VIEWS: { id: View; label: string; icon: ReactNode }[] = [
  { id: 'chat', label: 'Workbench', icon: <ChatIcon /> },
  { id: 'documents', label: 'Documents', icon: <DocIcon /> },
  { id: 'benchmark', label: 'Benchmarks', icon: <ChartIcon /> },
];

export default function SessionsRail({ view, onView }: {
  view: View; onView: (v: View) => void;
}) {
  const [list, setList] = useState<SessionSummary[]>([]);
  const [hp, setHp] = useState<HealthResponse | null>(null);
  const [open, setOpen] = useState(true);
  const clear = useSession((s) => s.clear);
  const loadSession = useSession((s) => s.loadSession);
  const activeId = useSession((s) => s.sessionId);
  const phase = useSession((s) => s.phase);

  useEffect(() => {
    const poll = () => {
      void health().then(setHp).catch(() => setHp(null));
      // The list too, so a session that just finished shows up without a
      // reload -- it used to be fetched once at mount and never again.
      void sessions().then(setList).catch(() => setList([]));
    };
    poll();
    const id = setInterval(poll, 5000);
    return () => clearInterval(id);
  }, []);

  return (
    <nav className="flex w-64 shrink-0 flex-col bg-steel-900 px-4 pt-5">
      <div className="flex items-center gap-3 border-b border-steel-800 px-1 pb-5">
        <Logo />
        <p className="text-xl font-semibold tracking-tight text-steel-100">
          Privis
        </p>
      </div>

      <button
        type="button"
        onClick={() => { clear(); onView('chat'); }}
        disabled={phase !== 'idle'}
        className="mt-5 flex w-full items-center justify-center gap-2 rounded-xl
                   bg-steel-100 px-4 py-3 text-sm font-semibold text-steel-900
                   shadow-sm transition-opacity hover:opacity-90
                   disabled:opacity-50"
      >
        New Chat <EditIcon />
      </button>

      <ul className="mt-4 space-y-1">
        {VIEWS.map((v) => {
          const active = view === v.id;
          return (
            <li key={v.id}>
              <button
                type="button"
                onClick={() => onView(v.id)}
                className={`relative flex w-full items-center gap-3 rounded-xl
                            px-3 py-2.5 text-left text-sm transition-colors ${
                  active
                    ? 'bg-accent-deep font-medium text-steel-100'
                    : 'text-steel-400 hover:bg-steel-850 hover:text-steel-200'}`}
              >
                <span className={active ? 'text-accent' : ''}>{v.icon}</span>
                {v.label}
                {active && (
                  <span className="absolute inset-y-1 right-0 w-1.5 rounded-full
                                   bg-brand" aria-hidden />
                )}
              </button>
            </li>
          );
        })}
      </ul>

      <div className="mt-4 border-t border-steel-800 pt-4">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="flex w-full items-center justify-between px-1 text-sm
                     font-semibold text-steel-200"
        >
          Recent
          <ChevronIcon className={`h-4 w-4 text-steel-500 transition-transform
                                   ${open ? '' : '-rotate-90'}`} />
        </button>
      </div>

      <ul className={`mt-2 min-h-0 flex-1 overflow-y-auto scroll-thin
                      ${open ? '' : 'invisible'}`}>
        {list.length === 0 && (
          <li className="px-1 py-2 text-tiny text-steel-500">
            No stored sessions.
          </li>
        )}
        {list.map((s) => (
          <li key={s.id}>
            <button
              type="button"
              onClick={() => { onView('chat'); void loadSession(s.id); }}
              disabled={phase !== 'idle'}
              title={phase !== 'idle' ? 'wait for the current answer to finish' : s.title}
              className={`w-full rounded-lg px-2 py-2 text-left transition-colors
                          disabled:opacity-50 ${
                s.id === activeId
                  ? 'bg-steel-850 text-steel-100'
                  : 'text-steel-300 hover:bg-steel-850'}`}
            >
              <p className="truncate text-sm">{s.title}</p>
              <p className="text-micro text-steel-500">
                {new Date(s.created_at * 1000).toLocaleDateString()} ·{' '}
                {s.message_count} msg
              </p>
            </button>
          </li>
        ))}
      </ul>

      <div className="relative my-4 overflow-hidden rounded-2xl border
                      border-steel-800 bg-steel-900 p-4 shadow-card">
        {/* The diagonal strokes from the brand card, drawn in CSS. */}
        <div className="pointer-events-none absolute -right-3 -top-3 flex
                        rotate-45 gap-1.5 opacity-60" aria-hidden>
          <span className="h-16 w-2 rounded-full bg-accent-dim" />
          <span className="h-16 w-2 rounded-full bg-accent-dim" />
        </div>
        <span className="inline-flex h-8 w-8 items-center justify-center
                         rounded-lg bg-brand text-white">
          <ShieldIcon className="h-4 w-4" />
        </span>
        <p className="mb-2 mt-2 text-sm font-semibold text-steel-100">
          System status
        </p>
        <div className="space-y-1.5">
          <Health label="Backend" ok={hp?.ok ?? false}
                  value={hp ? 'up' : 'unreachable'} />
          <Health label="Qdrant" ok={hp?.qdrant ?? false}
                  value={hp?.qdrant ? 'up' : 'down'} />
          <Health label="Model" ok={Boolean(hp?.model_loaded)}
                  value={hp?.model_loaded ?? 'none'} />
          <Health label="VRAM free" ok={(hp?.vram_free_mb ?? 0) > 0}
                  value={hp ? `${hp.vram_free_mb.toLocaleString()} MB` : '—'} />
        </div>
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
        <span className="truncate text-micro text-steel-400" title={value}>
          {value}
        </span>
      </span>
    </div>
  );
}
