/**
 * Layout shell. Owner: person 1.
 *
 * Three regions, all visible at once on a 1920x1080 projector. Nothing that
 * gets demonstrated is behind a tab or a scroll:
 *
 *   rail (56) | chat + composer (flex) | instrument column (fixed 22rem)
 *
 * The instrument column is ordered by how often a judge looks at it: router
 * (demo #1), then the live trace, then artifacts, with the network monitor
 * pinned to the bottom where it never moves and never scrolls away (demo #5).
 */
import { useState } from 'react';
import SessionsRail from './panels/SessionsRail';
import type { View } from './panels/SessionsRail';
import RouterPanel from './panels/RouterPanel';
import TracePanel from './panels/TracePanel';
import ArtifactsPanel from './panels/ArtifactsPanel';
import NetworkPanel from './panels/NetworkPanel';
import ChatView from './views/ChatView';
import DocumentsView from './views/DocumentsView';
import BenchmarkView from './views/BenchmarkView';

export default function App() {
  const [view, setView] = useState<View>('chat');

  return (
    <div className="flex h-full w-full overflow-hidden">
      <SessionsRail view={view} onView={setView} />

      <main className="flex min-w-0 flex-1 flex-col border-r border-steel-800
                       bg-steel-950/60">
        {view === 'chat' && <ChatView />}
        {view === 'documents' && <DocumentsView />}
        {view === 'benchmark' && <BenchmarkView />}
      </main>

      {/* Instrument column. Always mounted, even on the documents and
          benchmark views: the network monitor must never leave the screen. */}
      <aside className="flex w-[22rem] shrink-0 flex-col gap-2 overflow-hidden
                        bg-steel-900/40 p-2">
        <RouterPanel className="shrink-0" />
        {/* flex-1: the trace takes the slack so the network monitor
            is pinned to the bottom edge rather than floating. */}
        <TracePanel className="flex-1" />
        <ArtifactsPanel className="max-h-64 shrink-0" />
        <NetworkPanel />
      </aside>
    </div>
  );
}
