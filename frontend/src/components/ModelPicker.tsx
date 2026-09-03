/**
 * Model picker and swap progress. Owner: person 1.
 *
 * Two things sit here because they are the same story told twice: which model
 * is about to answer, and what it costs to change your mind.
 *
 * The picker defaults to Auto. Auto is not a model -- it means no `model_id`
 * goes on the request at all, so the router behaves exactly as it did before
 * this control existed. Choosing a model pins the conversation to it.
 *
 * SwapProgress exists because a swap takes 9-19 seconds on the demo card. The
 * stream already carries everything needed to show that honestly: `eta_s` on
 * model.loading and `load_ms` on model.ready. Silence for fifteen seconds
 * reads as a hang, and a spinner with no numbers reads as a slow hang.
 */
import { useEffect, useRef, useState } from 'react';

import { useSession } from '../store/session';

/** VRAM in GB, because 5903 MB is a number nobody weighs a decision with. */
function gb(mb: number): string {
  return `${(mb / 1024).toFixed(1)} GB`;
}

function ctx(tokens: number): string {
  return tokens >= 1024 ? `${Math.round(tokens / 1024)}K ctx` : `${tokens} ctx`;
}

export function ModelPicker() {
  const models = useSession((s) => s.models);
  const selected = useSession((s) => s.selectedModel);
  const select = useSession((s) => s.selectModel);
  const loadModels = useSession((s) => s.loadModels);
  const busy = useSession((s) => s.phase !== 'idle');

  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => { loadModels(); }, [loadModels]);

  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    }
    function onEsc(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onEsc);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onEsc);
    };
  }, [open]);

  const current = models.find((m) => m.id === selected) ?? null;
  const resident = models.find((m) => m.loaded) ?? null;
  const label = current ? (current.display_name || current.id) : 'Auto';

  // No registry, no picker. The composer still sends and the router still
  // routes; a dead dropdown would only invite clicking it.
  if (models.length === 0) return null;

  return (
    <div ref={root} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={busy}
        title={current ? `Pinned to ${current.id}` : 'The router picks per message'}
        className="flex h-7 max-w-[15rem] items-center gap-1.5 border
                   border-steel-700 bg-steel-850 px-2 font-mono text-tiny
                   text-steel-300 hover:border-accent-dim hover:text-accent
                   disabled:opacity-40"
      >
        <span className={current ? 'text-accent' : 'text-steel-500'}>
          {current ? '◆' : '◇'}
        </span>
        <span className="truncate">{label}</span>
        {current && (
          <span className="shrink-0 text-steel-600">{gb(current.vram_mb)}</span>
        )}
        <span className="shrink-0 text-steel-600">{open ? '▾' : '▸'}</span>
      </button>

      {open && (
        <div
          className="absolute bottom-9 left-0 z-20 w-[26rem] border
                     border-steel-700 bg-steel-900 shadow-xl"
          role="listbox"
        >
          <Option
            active={selected === null}
            title="Auto"
            subtitle={
              resident
                ? `The router chooses per message. ${resident.display_name || resident.id} is resident.`
                : 'The router chooses per message.'
            }
            onClick={() => { select(null); setOpen(false); }}
          />
          <div className="border-t border-steel-850" />
          {models.map((m) => (
            <Option
              key={m.id}
              active={selected === m.id}
              loaded={m.loaded}
              title={m.display_name || m.id}
              subtitle={m.description}
              meta={`${gb(m.vram_mb)} · ${ctx(m.context)}`}
              capabilities={m.capabilities}
              onClick={() => { select(m.id); setOpen(false); }}
            />
          ))}
          <p className="border-t border-steel-850 px-3 py-1.5 font-mono
                        text-micro text-steel-600">
            One model is resident at a time. Switching costs a reload.
          </p>
        </div>
      )}
    </div>
  );
}

function Option({
  active, loaded = false, title, subtitle, meta, capabilities = [], onClick,
}: {
  active: boolean;
  loaded?: boolean;
  title: string;
  subtitle?: string;
  meta?: string;
  capabilities?: string[];
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="option"
      aria-selected={active}
      onClick={onClick}
      className={`block w-full px-3 py-2 text-left hover:bg-steel-850
                  ${active ? 'bg-steel-850' : ''}`}
    >
      <div className="flex items-baseline gap-2">
        <span className={`font-mono text-tiny ${active ? 'text-accent' : 'text-steel-100'}`}>
          {title}
        </span>
        {loaded && (
          <span className="border border-iso-dim px-1 font-mono text-micro text-iso">
            resident
          </span>
        )}
        {meta && (
          <span className="ml-auto shrink-0 font-mono text-micro text-steel-500">
            {meta}
          </span>
        )}
      </div>
      {subtitle && (
        <p className="mt-0.5 text-tiny leading-snug text-steel-400">{subtitle}</p>
      )}
      {capabilities.length > 0 && (
        <ul className="mt-1 flex flex-wrap gap-1">
          {capabilities.map((c) => (
            <li key={c}
                className="border border-steel-700 px-1 font-mono text-micro
                           text-steel-500">
              {c}
            </li>
          ))}
        </ul>
      )}
    </button>
  );
}

/**
 * Real progress across a model swap, from the events the stream already sends.
 *
 * The bar is driven by elapsed/eta and deliberately stops at 95%: the only
 * thing that may claim completion is `model.ready`. A bar that sits full while
 * nothing happens is worse than one that sits at 95% and says why.
 */
export function SwapProgress() {
  const swap = useSession((s) => s.swap);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!swap) return;
    const id = window.setInterval(() => setNow(Date.now()), 200);
    return () => window.clearInterval(id);
  }, [swap]);

  if (!swap) return null;

  const elapsed = Math.max(0, (now - swap.startedAt) / 1000);
  const eta = Math.max(1, swap.etaS);
  const overrun = elapsed > eta;
  const pct = Math.min(95, (elapsed / eta) * 95);

  return (
    <div className="border-t border-steel-800 bg-steel-900 px-3 py-2">
      <div className="flex items-baseline justify-between font-mono text-tiny">
        <span className="text-steel-200">
          Loading <span className="text-accent">{swap.modelId}</span>
          {swap.evicting && (
            <span className="text-steel-500"> · evicting {swap.evicting}</span>
          )}
        </span>
        <span className={overrun ? 'text-work' : 'text-steel-500'}>
          {elapsed.toFixed(1)}s {overrun ? `· past the ${eta}s estimate` : `/ ~${eta}s`}
        </span>
      </div>
      <div className="mt-1.5 h-1 w-full bg-steel-850">
        <div
          className={`h-full transition-[width] duration-200 ${
            overrun ? 'bg-work' : 'bg-accent'
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="mt-1 font-mono text-micro text-steel-600">
        One model is resident at a time; the card has room for one.
      </p>
    </div>
  );
}
