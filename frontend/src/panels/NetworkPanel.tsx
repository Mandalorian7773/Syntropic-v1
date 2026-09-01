/**
 * Network monitor. Owner: person 1.
 *
 * Required demonstration #5, and the sponsor calls it the actual proof of the
 * sovereign claim. Design rules that follow from that:
 *
 *   - permanent space, never a tab, never behind a scroll
 *   - the counter is the largest text in the entire application, readable from
 *     four metres on a projector
 *   - green while zero, red the instant it is not -- AND IT STAYS RED. A
 *     breach that scrolls away is a breach nobody saw. `breached` is one-way.
 */
import { useEffect, useRef, useState } from 'react';
import type { NetworkStatus } from '../types/events';
import { networkStatus } from '../api/rest';
import { Dot } from '../components/ui';

const POLL_MS = 2000;

export default function NetworkPanel() {
  const [status, setStatus] = useState<NetworkStatus | null>(null);
  const [reachable, setReachable] = useState(true);
  const [elapsed, setElapsed] = useState(0);
  // One-way latch: once true, never false again for the life of the tab.
  const breached = useRef(false);

  useEffect(() => {
    let live = true;
    const poll = async () => {
      try {
        const next = await networkStatus();
        if (!live) return;
        if (next.external_packets > 0 || next.dns_queries > 0) {
          breached.current = true;
        }
        setStatus(next);
        setReachable(true);
      } catch {
        if (live) setReachable(false);
      }
    };
    void poll();
    const id = setInterval(poll, POLL_MS);
    return () => { live = false; clearInterval(id); };
  }, []);

  // Tick the "since" clock every second so the panel is visibly alive even
  // when the counters never move -- which is exactly what we hope for.
  useEffect(() => {
    if (!status) return;
    const tick = () => setElapsed(Math.max(0, Date.now() / 1000 - status.since));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [status?.since]);

  const total = (status?.external_packets ?? 0) + (status?.dns_queries ?? 0);
  const bad = breached.current || total > 0;

  return (
    <section
      className={`panel shrink-0 border-2 ${
        bad ? 'border-fault bg-fault-deep/40' : 'border-iso-dim bg-iso-deep/25'
      }`}
    >
      <header className="panel-head border-b-0 bg-transparent">
        <h2 className="label">Network isolation</h2>
        <span className="flex items-center gap-1.5">
          <Dot tone={!reachable ? 'work' : bad ? 'fault' : 'iso'} />
          <span className="label">
            {!reachable ? 'monitor offline' : bad ? 'breach' : 'monitoring'}
          </span>
        </span>
      </header>

      <div className="px-3 pb-3 pt-1">
        {/* The number. Everything else on this panel is subordinate to it. */}
        <div
          className={`text-center font-mono font-bold leading-none tabular-nums
                      ${bad ? 'text-fault' : 'text-iso'}`}
          style={{ fontSize: 'clamp(2.75rem, 4.4vw, 4.5rem)' }}
        >
          {total}
        </div>
        <div
          className={`mt-1 text-center font-mono font-semibold uppercase
                      tracking-[0.22em] ${bad ? 'text-fault' : 'text-iso'}`}
          style={{ fontSize: 'clamp(0.7rem, 0.95vw, 1rem)' }}
        >
          External calls
        </div>

        <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1 border-t
                       border-steel-800 pt-2 font-mono text-tiny">
          <Stat label="Packets" value={status?.external_packets ?? '—'} bad={bad} />
          <Stat label="DNS" value={status?.dns_queries ?? '—'} bad={bad} />
          <Stat label="Uptime" value={status ? duration(elapsed) : '—'} />
          <Stat
            label="Rules"
            value={status?.rules_active ? 'ACTIVE' : 'INACTIVE'}
            bad={status ? !status.rules_active : false}
          />
        </dl>

        {bad && (
          <p className="mt-2 border border-fault-dim bg-fault-deep px-2 py-1
                        font-mono text-tiny text-fault">
            Egress detected. This indicator does not reset.
          </p>
        )}
        {!reachable && (
          <p className="mt-2 font-mono text-tiny text-work">
            /api/network/status unreachable — monitor cannot confirm isolation.
          </p>
        )}
      </div>
    </section>
  );
}

function Stat({ label, value, bad = false }: {
  label: string; value: string | number; bad?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between">
      <dt className="label">{label}</dt>
      <dd className={`tabular-nums ${bad ? 'text-fault' : 'text-steel-200'}`}>
        {value}
      </dd>
    </div>
  );
}

function duration(seconds: number): string {
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h > 0
    ? `${h}h ${String(m).padStart(2, '0')}m`
    : `${m}:${String(sec).padStart(2, '0')}`;
}
