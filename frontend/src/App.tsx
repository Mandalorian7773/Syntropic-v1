/**
 * Layout shell. Owner: person 1.
 *
 * Three regions, all visible at once on a 1920x1080 projector. Nothing that
 * gets demonstrated is behind a tab or a scroll:
 *
 *   rail (64) | header over [chat + composer (flex) | instruments (22rem)]
 *
 * The instrument column is ordered by how often a judge looks at it: router
 * (demo #1), then the live trace, then artifacts.
 */
import { useState } from 'react';
import SessionsRail from './panels/SessionsRail';
import { VIEWS } from './panels/SessionsRail';
import type { View } from './panels/SessionsRail';
import { useTheme } from './store/theme';
import { BulbIcon, MoonIcon, ShieldIcon, SunIcon } from './components/icons';
import RouterPanel from './panels/RouterPanel';
import TracePanel from './panels/TracePanel';
import ArtifactsPanel from './panels/ArtifactsPanel';
import ChatView from './views/ChatView';
import DocumentsView from './views/DocumentsView';
import BenchmarkView from './views/BenchmarkView';

export default function App() {
  const [view, setView] = useState<View>('chat');

  return (
    <div className="flex h-full w-full overflow-hidden bg-steel-950">
      <SessionsRail view={view} onView={setView} />

      <div className="flex min-w-0 flex-1 flex-col border-l border-steel-800">
        <Header view={view} />

        <div className="flex min-h-0 flex-1 gap-4 px-5 pb-5">
          <main className="panel canvas-dots flex min-w-0 flex-1 flex-col
                           overflow-hidden">
            {view === 'chat' && <ChatView />}
            {view === 'documents' && <DocumentsView />}
            {view === 'benchmark' && <BenchmarkView />}
          </main>

          {/* Instrument column. Always mounted, even on the documents and
              benchmark views. */}
          <aside className="flex w-[22rem] shrink-0 flex-col gap-3 overflow-hidden">
            <RouterPanel className="shrink-0" />
            {/* flex-1: the trace takes the slack the network monitor used to
                occupy, so artifacts stay pinned to the bottom edge. */}
            <TracePanel className="flex-1" />
            <ArtifactsPanel className="max-h-64 shrink-0" />
          </aside>
        </div>
      </div>
    </div>
  );
}

function Header({ view }: { view: View }) {
  const theme = useTheme((s) => s.theme);
  const toggle = useTheme((s) => s.toggle);
  const label = VIEWS.find((v) => v.id === view)?.label ?? '';
  const dark = theme === 'dark';

  return (
    <header className="flex shrink-0 items-center gap-4 px-5 py-4">
      <span className="inline-flex h-11 w-11 items-center justify-center
                       rounded-xl border border-steel-800 bg-steel-900
                       text-steel-400 shadow-card">
        <BulbIcon className="h-5 w-5" />
      </span>
      <p className="min-w-0 truncate text-2xl tracking-tight text-steel-500">
        Privis <span className="text-steel-600">/</span>{' '}
        <span className="text-steel-100">{label}</span>
      </p>

      <div className="ml-auto flex items-center gap-3">
        <span className="hidden items-center gap-2 rounded-xl bg-accent-deep
                         px-3 py-2 text-tiny font-medium text-accent md:flex">
          <ShieldIcon className="h-4 w-4" /> Runs locally
        </span>
        <button
          type="button"
          onClick={toggle}
          role="switch"
          aria-checked={dark}
          aria-label="Dark mode"
          title={dark ? 'Switch to light mode' : 'Switch to dark mode'}
          className="relative flex h-10 w-[76px] items-center rounded-xl border
                     border-steel-800 bg-steel-900 p-1 shadow-card"
        >
          <span className={`absolute top-1 h-8 w-8 rounded-lg bg-brand shadow-sm
                            transition-transform duration-200
                            ${dark ? 'translate-x-[34px]' : 'translate-x-0'}`} />
          <span className={`relative z-10 flex h-8 w-8 items-center justify-center
                            ${dark ? 'text-steel-500' : 'text-white'}`}>
            <SunIcon className="h-4 w-4" />
          </span>
          <span className={`relative z-10 flex h-8 w-8 items-center justify-center
                            ${dark ? 'text-white' : 'text-steel-500'}`}>
            <MoonIcon className="h-4 w-4" />
          </span>
        </button>
      </div>
    </header>
  );
}
