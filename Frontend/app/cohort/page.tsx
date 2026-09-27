'use client';

import { useMemo, useState } from 'react';
import useSWR from 'swr';
import { ArrowLeft, Loader2, RefreshCw, ShieldAlert } from 'lucide-react';
import ClinicalDashboard from '@/components/dashboard/ClinicalDashboard';
import StatTile from '@/components/dashboard/StatTile';
import { getJobStatus, listJobs } from '@/lib/api';
import { riskStyles } from '@/lib/utils/pipeline';
import type { JobSummary } from '@/types/api';

const RISK_ORDER = ['HIGH', 'MODERATE', 'INDETERMINATE', 'LOW'];

function fmtDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
}

function fmtDuration(s?: number | null): string {
  if (s == null) return '—';
  return s < 60 ? `${s.toFixed(1)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
}

function RiskBadge({ level }: { level?: string | null }) {
  if (!level) return <span className="text-xs text-slate-400">—</span>;
  const st = riskStyles(level);
  return <span className={`inline-flex rounded-md border px-2 py-0.5 text-xs font-bold ${st.badge} ${st.text}`}>{level}</span>;
}

export default function CohortPage() {
  const { data: jobs, error, isLoading, mutate } = useSWR('jobs', () => listJobs(500), { refreshInterval: 10_000 });
  const [selected, setSelected] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>('');
  const [latestOnly, setLatestOnly] = useState(true);
  const [showTests, setShowTests] = useState(false);
  const { data: job, isLoading: jobLoading } = useSWR(selected ? ['job', selected] : null, () => getJobStatus(selected!));

  // Test and benchmark runs (smoke test, benchmark suites) are hidden by default
  const isTest = (j: JobSummary) => /^(BENCH_|SMOKE)/.test(j.patient_id);
  const visible = useMemo(() => (jobs ?? []).filter((j) => showTests || !isTest(j)), [jobs, showTests]);

  // Latest completed analysis per patient (the history keeps every run)
  const latest = useMemo(() => {
    const seen = new Map<string, JobSummary>();
    for (const j of visible) if (j.status === 'completed' && !seen.has(j.patient_id)) seen.set(j.patient_id, j);
    return Array.from(seen.values());
  }, [visible]);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const j of latest) c[j.risk_level ?? '—'] = (c[j.risk_level ?? '—'] ?? 0) + 1;
    return c;
  }, [latest]);

  const base = latestOnly ? [...latest, ...visible.filter((j) => j.status !== 'completed' && !latest.some((l) => l.patient_id === j.patient_id))] : visible;
  const rows = base
    .filter((j) => !filter || j.risk_level === filter || (filter === 'failed' && j.status === 'failed'))
    .sort((a, b) => b.created_at.localeCompare(a.created_at));

  if (selected) {
    return (
      <div className="space-y-6">
        <button
          type="button"
          onClick={() => setSelected(null)}
          className="inline-flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
        >
          <ArrowLeft className="h-4 w-4" /> Back to cohort
        </button>
        {jobLoading || !job ? (
          <p className="flex items-center gap-2 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" /> Loading report…</p>
        ) : job.result ? (
          <ClinicalDashboard report={job.result} job={job} />
        ) : (
          <p className="rounded-xl border border-slate-200 p-4 text-sm text-slate-600 dark:border-slate-800 dark:text-slate-300">
            No report for this job ({job.status}). {job.error}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-dna-600 dark:text-dna-400">Analysis history</p>
          <h1 className="mt-1 text-2xl font-bold tracking-tight text-slate-900 dark:text-white">Cohort</h1>
          <p className="mt-1 max-w-3xl text-sm text-slate-600 dark:text-slate-400">
            Every analysis run on this server. The demonstration cohort contains real open-access genomes from the 1000 Genomes
            Project (30× reads over the panel genes) and the GIAB NA12878 reference exome.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void mutate()}
          className="inline-flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
        >
          <RefreshCw className="h-4 w-4" /> Refresh
        </button>
      </header>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <StatTile label="Patients analysed" value={String(latest.length)} hint={`${visible.length} runs shown`} />
        {RISK_ORDER.map((r) => (
          <StatTile key={r} label={`Risk ${r}`} value={String(counts[r] ?? 0)} hint="latest run per patient" />
        ))}
      </div>

      <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-card dark:border-slate-800 dark:bg-slate-900 dark:shadow-card-dark">
        <div className="mb-4 flex flex-wrap items-center gap-2">
          {['', ...RISK_ORDER, 'failed'].map((f) => (
            <button
              key={f || 'all'}
              type="button"
              onClick={() => setFilter(f)}
              aria-pressed={filter === f}
              className={`rounded-full border px-3 py-1 text-xs font-medium ${
                filter === f
                  ? 'border-slate-300 bg-slate-100 text-slate-900 dark:border-slate-600 dark:bg-slate-800 dark:text-white'
                  : 'border-slate-200 text-slate-500 dark:border-slate-800'
              }`}
            >
              {f === '' ? 'All' : f === 'failed' ? 'Failed' : f}
            </button>
          ))}
          <span className="mx-1 h-5 w-px bg-slate-200 dark:bg-slate-700" aria-hidden />
          <label className="flex items-center gap-1.5 text-xs text-slate-600 dark:text-slate-300">
            <input type="checkbox" checked={latestOnly} onChange={(e) => setLatestOnly(e.target.checked)} className="accent-dna-600" />
            Latest run per patient
          </label>
          <label className="flex items-center gap-1.5 text-xs text-slate-600 dark:text-slate-300">
            <input type="checkbox" checked={showTests} onChange={(e) => setShowTests(e.target.checked)} className="accent-dna-600" />
            Show test &amp; benchmark runs
          </label>
        </div>

        {error && <p className="text-sm text-red-600">Could not load the job history.</p>}
        {isLoading && <p className="flex items-center gap-2 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" /> Loading…</p>}

        {jobs && (
          <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
            <table className="w-full min-w-[820px] text-left text-sm">
              <thead className="bg-slate-50 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                <tr>
                  <th className="px-4 py-2.5 font-semibold">Patient</th>
                  <th className="px-4 py-2.5 font-semibold">Mode</th>
                  <th className="px-4 py-2.5 font-semibold">Risk</th>
                  <th className="px-4 py-2.5 font-semibold">Genes with P/LP variants</th>
                  <th className="px-4 py-2.5 text-right font-semibold">Panel variants</th>
                  <th className="px-4 py-2.5 text-right font-semibold">Duration</th>
                  <th className="px-4 py-2.5 font-semibold">Started</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {rows.map((j) => (
                  <tr
                    key={j.job_id}
                    onClick={() => j.status === 'completed' && setSelected(j.job_id)}
                    className={j.status === 'completed' ? 'cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-800/50' : 'opacity-70'}
                  >
                    <td className="px-4 py-2.5 font-mono text-xs font-semibold">{j.patient_id}</td>
                    <td className="px-4 py-2.5 text-xs uppercase text-slate-500">{j.mode ?? '—'}</td>
                    <td className="px-4 py-2.5">
                      {j.status === 'completed' ? <RiskBadge level={j.risk_level} /> : <span className="text-xs text-slate-500">{j.status}</span>}
                    </td>
                    <td className="px-4 py-2.5">
                      {j.identified_genes.length ? (
                        <span className="inline-flex items-center gap-1.5 font-mono text-xs font-semibold">
                          <ShieldAlert className="h-3.5 w-3.5" style={{ color: 'var(--viz-critical)' }} aria-hidden />
                          {j.identified_genes.join(', ')}
                        </span>
                      ) : (
                        <span className="text-xs text-slate-400">none</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{j.variants_in_panel ?? '—'}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{fmtDuration(j.duration_s)}</td>
                    <td className="px-4 py-2.5 text-xs text-slate-500">{fmtDate(j.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
