/**
 * Scaffold page. Owner: person 1.
 *
 * It exists to prove one thing: an SSE stream opened from the browser reaches
 * this component and the frames type-check against the GENERATED contract
 * types. Person 1 replaces this entirely with the real three-panel workbench.
 */
import { useState } from 'react';
import type { Event } from './types/events';
import { streamChat } from './api/sse';

export default function App() {
  const [events, setEvents] = useState<Event[]>([]);
  const [streaming, setStreaming] = useState(false);

  async function run() {
    setEvents([]);
    setStreaming(true);
    await streamChat({ message: 'scaffold ping' }, (ev) =>
      setEvents((prev) => [...prev, ev]),
    );
    setStreaming(false);
  }

  return (
    <div className="min-h-screen bg-neutral-950 p-8 font-mono text-sm text-neutral-200">
      <h1 className="mb-1 text-lg text-neutral-50">SIH26117 workbench — scaffold</h1>
      <p className="mb-4 text-neutral-500">
        Stub page. Streams from the mock server and prints raw contract events.
      </p>

      <button
        onClick={run}
        disabled={streaming}
        className="mb-6 rounded border border-neutral-700 px-3 py-1 hover:bg-neutral-800 disabled:opacity-40"
      >
        {streaming ? 'streaming…' : 'open stream'}
      </button>

      <ul className="space-y-1">
        {events.map((ev, i) => (
          <li key={i} className="whitespace-pre-wrap break-all text-neutral-400">
            <span className="text-emerald-400">{ev.type}</span> {JSON.stringify(ev)}
          </li>
        ))}
      </ul>
    </div>
  );
}
