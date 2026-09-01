/**
 * Benchmark view. Owner: person 1.
 *
 * Reads the STATIC file at public/bench-results.json. Deliberately not a
 * backend call: these are committed evidence produced by person 3's
 * bench/run.py, not live telemetry, and the demo must render them even if the
 * backend is down.
 */
import { useEffect, useState } from 'react';
import {
  Bar, BarChart, CartesianGrid, Cell, Legend, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts';
import { ms } from '../components/ui';

interface Tier {
  tier: string;
  model: string;
  general: number;
  document: number;
  code: number;
  data: number;
  vision: number;
  overall: number;
  median_latency_ms: number;
  p95_latency_ms: number;
  peak_vram_mb: number;
  load_ms: number;
  tok_per_s: number;
}

interface Bench {
  generated_ts: number;
  host: string;
  notes: string;
  tiers: Tier[];
}

const SERIES = [
  { key: 'general', color: '#5a6b7a' },
  { key: 'document', color: '#38bdf8' },
  { key: 'code', color: '#2ee59d' },
  { key: 'data', color: '#f2b134' },
  { key: 'vision', color: '#a855f7' },
] as const;

export default function BenchmarkView() {
  const [bench, setBench] = useState<Bench | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Relative path: served from the same origin as the SPA, bundled at build.
    fetch('bench-results.json')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${r.status}`))))
      .then((d: Bench) => setBench(d))
      .catch((e: unknown) =>
        setError(e instanceof Error ? e.message : String(e)));
  }, []);

  if (error) {
    return (
      <p className="p-6 font-mono text-tiny text-fault">
        Could not read public/bench-results.json — {error}
      </p>
    );
  }
  if (!bench) {
    return <p className="p-6 font-mono text-tiny text-steel-500">loading…</p>;
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto scroll-thin p-6">
      <header className="mb-1 flex items-baseline justify-between">
        <h1 className="font-mono text-sm uppercase tracking-widest text-steel-100">
          Benchmarks
        </h1>
        <span className="font-mono text-tiny text-steel-500">
          {new Date(bench.generated_ts * 1000).toLocaleDateString()}
        </span>
      </header>
      <p className="mb-5 font-mono text-micro text-steel-600">
        {bench.host} — {bench.notes}
      </p>

      <div className="panel mb-5 p-3">
        <p className="label mb-3">Task success rate by model tier (%)</p>
        <div className="h-72">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={bench.tiers}
                      margin={{ top: 4, right: 8, left: -18, bottom: 4 }}>
              <CartesianGrid stroke="#212b34" vertical={false} />
              <XAxis dataKey="tier" stroke="#5a6b7a"
                     tick={{ fontSize: 10, fontFamily: 'ui-monospace' }} />
              <YAxis domain={[0, 100]} stroke="#5a6b7a"
                     tick={{ fontSize: 10, fontFamily: 'ui-monospace' }} />
              <Tooltip
                cursor={{ fill: 'rgba(56,189,248,0.06)' }}
                contentStyle={{
                  background: '#141a20', border: '1px solid #2a3540',
                  fontFamily: 'ui-monospace', fontSize: 11,
                }}
                labelStyle={{ color: '#e6edf2' }}
              />
              <Legend wrapperStyle={{ fontSize: 10, fontFamily: 'ui-monospace' }} />
              {SERIES.map((s) => (
                <Bar key={s.key} dataKey={s.key} fill={s.color} name={s.key} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="panel mb-5 p-3">
        <p className="label mb-3">Overall success rate (%)</p>
        <div className="h-52">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={bench.tiers}
                      margin={{ top: 4, right: 8, left: -18, bottom: 4 }}>
              <CartesianGrid stroke="#212b34" vertical={false} />
              <XAxis dataKey="tier" stroke="#5a6b7a"
                     tick={{ fontSize: 10, fontFamily: 'ui-monospace' }} />
              <YAxis domain={[0, 100]} stroke="#5a6b7a"
                     tick={{ fontSize: 10, fontFamily: 'ui-monospace' }} />
              <Tooltip
                cursor={{ fill: 'rgba(56,189,248,0.06)' }}
                contentStyle={{
                  background: '#141a20', border: '1px solid #2a3540',
                  fontFamily: 'ui-monospace', fontSize: 11,
                }}
              />
              <Bar dataKey="overall" name="overall">
                {bench.tiers.map((t) => (
                  // The routed configuration is the point of the project, so it
                  // is the only bar in the accent colour.
                  <Cell key={t.tier}
                        fill={t.tier.startsWith('Routed') ? '#2ee59d' : '#3d4b58'} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="panel overflow-x-auto scroll-thin">
        <table className="w-full border-collapse font-mono text-tiny">
          <thead>
            <tr className="border-b border-steel-700 bg-steel-850">
              {['Tier', 'Model', 'Overall', 'Median', 'p95', 'Load', 'Peak VRAM', 'tok/s']
                .map((h, i) => (
                  <th key={h}
                      className={`label px-3 py-2 ${i < 2 ? 'text-left' : 'text-right'}`}>
                    {h}
                  </th>
                ))}
            </tr>
          </thead>
          <tbody>
            {bench.tiers.map((t) => {
              const routed = t.tier.startsWith('Routed');
              return (
                <tr key={t.tier}
                    className={`border-b border-steel-850 ${
                      routed ? 'bg-iso-deep/25' : ''}`}>
                  <td className={`px-3 py-2 ${
                    routed ? 'text-iso' : 'text-steel-200'}`}>{t.tier}</td>
                  <td className="px-3 py-2 text-steel-500">{t.model}</td>
                  <td className={`px-3 py-2 text-right tabular-nums ${
                    routed ? 'text-iso' : 'text-steel-200'}`}>{t.overall}%</td>
                  <td className="px-3 py-2 text-right tabular-nums text-steel-400">
                    {ms(t.median_latency_ms)}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-steel-400">
                    {ms(t.p95_latency_ms)}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-steel-400">
                    {ms(t.load_ms)}
                  </td>
                  <td className={`px-3 py-2 text-right tabular-nums ${
                    t.peak_vram_mb > 6000 ? 'text-fault' : 'text-steel-400'}`}>
                    {t.peak_vram_mb.toLocaleString()} MB
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-steel-400">
                    {t.tok_per_s.toFixed(1)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-2 font-mono text-micro text-steel-600">
        Peak VRAM is shown against an 8192 MB budget. Vision scores of 0 mean the
        tier has no vision capability, not that it failed.
      </p>
    </div>
  );
}
