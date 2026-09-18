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
import { useTheme } from '../store/theme';

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

const SERIES = ['general', 'document', 'code', 'data', 'vision'] as const;

// Recharts takes colours as SVG attributes, where CSS variables do not
// resolve, so each theme's chart palette is spelled out here.
const CHART = {
  light: {
    series: { general: '#94a3b8', document: '#0ea5e9', code: '#16a34a', data: '#f59e0b', vision: '#a855f7' },
    grid: '#e8ecea', axis: '#7a8480', cursor: 'rgba(22,163,74,0.06)',
    tipBg: '#ffffff', tipBorder: '#e8ecea', tipText: '#0f1412',
    routed: '#1ec446', other: '#d3d9d6',
  },
  dark: {
    series: { general: '#7a8483', document: '#38bdf8', code: '#4ade80', data: '#f2b134', vision: '#c084fc' },
    grid: '#262b2b', axis: '#7a8483', cursor: 'rgba(74,222,128,0.06)',
    tipBg: '#141717', tipBorder: '#3a4141', tipText: '#f3f6f5',
    routed: '#22c55e', other: '#3a4141',
  },
} as const;

export default function BenchmarkView() {
  const [bench, setBench] = useState<Bench | null>(null);
  const [error, setError] = useState<string | null>(null);
  const c = CHART[useTheme((t) => t.theme)];

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
      <p className="p-6 text-tiny text-fault">
        Could not read public/bench-results.json — {error}
      </p>
    );
  }
  if (!bench) {
    return <p className="p-6 text-tiny text-steel-500">loading…</p>;
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto scroll-thin p-6">
      <header className="mb-1 flex items-baseline justify-between">
        <h1 className="text-sm uppercase tracking-widest text-steel-100">
          Benchmarks
        </h1>
        <span className="text-tiny text-steel-500">
          {new Date(bench.generated_ts * 1000).toLocaleDateString()}
        </span>
      </header>
      <p className="mb-5 text-micro text-steel-600">
        {bench.host} — {bench.notes}
      </p>

      <div className="panel mb-5 p-4">
        <p className="label mb-3">Task success rate by model tier (%)</p>
        <div className="h-72">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={bench.tiers}
                      margin={{ top: 4, right: 8, left: -18, bottom: 4 }}>
              <CartesianGrid stroke={c.grid} vertical={false} />
              <XAxis dataKey="tier" stroke={c.axis}
                     tick={{ fontSize: 10 }} />
              <YAxis domain={[0, 100]} stroke={c.axis}
                     tick={{ fontSize: 10 }} />
              <Tooltip
                cursor={{ fill: c.cursor }}
                contentStyle={{
                  background: c.tipBg, border: `1px solid ${c.tipBorder}`, borderRadius: 12,
                  fontSize: 11,
                }}
                labelStyle={{ color: c.tipText }}
              />
              <Legend wrapperStyle={{ fontSize: 10 }} />
              {SERIES.map((k) => (
                <Bar key={k} dataKey={k} fill={c.series[k]} name={k}
                     radius={[4, 4, 0, 0]} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="panel mb-5 p-4">
        <p className="label mb-3">Overall success rate (%)</p>
        <div className="h-52">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={bench.tiers}
                      margin={{ top: 4, right: 8, left: -18, bottom: 4 }}>
              <CartesianGrid stroke={c.grid} vertical={false} />
              <XAxis dataKey="tier" stroke={c.axis}
                     tick={{ fontSize: 10 }} />
              <YAxis domain={[0, 100]} stroke={c.axis}
                     tick={{ fontSize: 10 }} />
              <Tooltip
                cursor={{ fill: c.cursor }}
                contentStyle={{
                  background: c.tipBg, border: `1px solid ${c.tipBorder}`, borderRadius: 12,
                  fontSize: 11,
                }}
              />
              <Bar dataKey="overall" name="overall" radius={[6, 6, 0, 0]}>
                {bench.tiers.map((t) => (
                  // The routed configuration is the point of the project, so it
                  // is the only bar in the accent colour.
                  <Cell key={t.tier}
                        fill={t.tier.startsWith('Routed') ? c.routed : c.other} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="panel overflow-x-auto scroll-thin">
        <table className="w-full border-collapse text-tiny">
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
      <p className="mt-2 text-micro text-steel-600">
        Peak VRAM is shown against an 8192 MB budget. Vision scores of 0 mean the
        tier has no vision capability, not that it failed.
      </p>
    </div>
  );
}
