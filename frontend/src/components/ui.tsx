/**
 * The handful of primitives every panel shares. Owner: person 1.
 * Deliberately tiny -- a component library would be a vendoring problem for
 * four elements.
 */
import type { ReactNode } from 'react';

export function Panel({
  title, right, children, className = '', bodyClass = '',
}: {
  title: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClass?: string;
}) {
  return (
    <section className={`panel flex min-h-0 flex-col ${className}`}>
      <header className="panel-head shrink-0">
        <h2 className="label">{title}</h2>
        {right}
      </header>
      <div className={`min-h-0 flex-1 overflow-y-auto scroll-thin ${bodyClass}`}>
        {children}
      </div>
    </section>
  );
}

/** Row of label + value, monospace value. The workhorse of the router panel. */
export function Field({ label, children, mono = true }: {
  label: string; children: ReactNode; mono?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <span className="label shrink-0">{label}</span>
      <span className={`min-w-0 truncate text-right text-tiny text-steel-200
                        ${mono ? 'font-mono' : ''}`}>
        {children}
      </span>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <p className="px-3 py-6 text-center font-mono text-tiny text-steel-600">
      {children}
    </p>
  );
}

/** Indeterminate progress: a bar that sweeps. Used only for waiting states. */
export function Sweep({ tone = 'work' }: { tone?: 'work' | 'accent' }) {
  const color = tone === 'work' ? 'bg-work' : 'bg-accent';
  return (
    <div className="h-0.5 w-full overflow-hidden bg-steel-800">
      <div className={`h-full w-1/4 ${color} animate-sweep`} />
    </div>
  );
}

export function Dot({ tone }: { tone: 'iso' | 'work' | 'fault' | 'idle' }) {
  const map = {
    iso: 'bg-iso', work: 'bg-work animate-pulse-slow',
    fault: 'bg-fault', idle: 'bg-steel-600',
  } as const;
  return <span className={`inline-block h-2 w-2 rounded-full ${map[tone]}`} />;
}

export function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function ms(n: number): string {
  return n < 1000 ? `${n} ms` : `${(n / 1000).toFixed(1)} s`;
}
