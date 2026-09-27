import { AlertTriangle } from 'lucide-react';
import BarList from '@/components/charts/BarList';
import ChartCard from '@/components/charts/ChartCard';
import StatTile, { StatusPill } from '@/components/dashboard/StatTile';
import { fmtCompact, fmtInt, fmtNum, fmtPct } from '@/lib/utils/stats';
import type { VcfStatistics } from '@/types/api';

function fmtValue(id: string, v: number | null): string {
  if (v == null) return '—';
  if (id.endsWith('rate') || id.includes('coverage')) return fmtPct(v);
  if (id === 'median_dp') return `${fmtNum(v, Number.isInteger(v) ? 0 : 1)}×`;
  return fmtNum(v, id === 'het_vaf_median' ? 3 : 2);
}

export default function QualityPanel({ stats, warnings }: { stats: VcfStatistics; warnings: string[] }) {
  const aln = stats.alignment;
  const cov = aln?.clinvar_sites_coverage;
  const counts = { OK: 0, WARN: 0, NA: 0 } as Record<'OK' | 'WARN' | 'NA', number>;
  stats.quality_checks.forEach((c) => (counts[c.status] += 1));

  return (
    <div className="space-y-5">
      <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-card dark:border-slate-800 dark:bg-slate-900 dark:shadow-card-dark">
        <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="text-sm font-bold text-slate-900 dark:text-white">Expert quality checks</h3>
            <p className="mt-0.5 max-w-2xl text-xs text-slate-500 dark:text-slate-400">
              Each metric is compared with the range expected for a human germline sample. A warning flags data to review;
              only coverage of known pathogenic sites enters the risk rules.
            </p>
          </div>
          <p className="text-xs text-slate-600 dark:text-slate-300">
            {counts.OK} pass · {counts.WARN} warning(s) · {counts.NA} not assessable
          </p>
        </header>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-left text-xs">
            <thead className="text-slate-500">
              <tr className="border-b border-slate-200 dark:border-slate-700">
                <th className="py-2 pr-3 font-semibold">Check</th>
                <th className="py-2 pr-3 text-right font-semibold">Value</th>
                <th className="py-2 pr-3 font-semibold">Expected</th>
                <th className="py-2 pr-3 font-semibold">Status</th>
                <th className="py-2 font-semibold">Interpretation</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {stats.quality_checks.map((c) => (
                <tr key={c.id} className="align-top">
                  <td className="py-2.5 pr-3 font-medium text-slate-800 dark:text-slate-100">{c.label}</td>
                  <td className="py-2.5 pr-3 text-right font-semibold tabular-nums text-slate-900 dark:text-white">{fmtValue(c.id, c.value)}</td>
                  <td className="py-2.5 pr-3 tabular-nums text-slate-600 dark:text-slate-300">{c.expected}</td>
                  <td className="py-2.5 pr-3"><StatusPill status={c.status} /></td>
                  <td className="py-2.5 leading-relaxed text-slate-500 dark:text-slate-400">{c.explanation}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {warnings.length > 0 && (
          <ul className="mt-4 space-y-1.5 rounded-xl border border-amber-500/30 bg-amber-500/5 p-3">
            {warnings.map((w) => (
              <li key={w} className="flex gap-2 text-xs text-slate-700 dark:text-slate-200">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" style={{ color: 'var(--viz-warning)' }} aria-hidden />
                {w}
              </li>
            ))}
          </ul>
        )}
      </section>

      {aln ? (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <StatTile label="Sequenced reads" value={fmtCompact(aln.total_reads)} hint={`${fmtInt(aln.read_pairs_examined)} read pairs examined`} />
            <StatTile label="Mapped to hg38" value={fmtPct(aln.mapped_rate, 2)} hint="expected ≥ 95%" status={stats.quality_checks.find((c) => c.id === 'mapped_rate')?.status} />
            <StatTile label="Properly paired" value={fmtPct(aln.properly_paired_rate, 2)} />
            <StatTile label="Duplicates (MarkDuplicates)" value={fmtPct(aln.duplication_rate)} hint="expected ≤ 30%" status={stats.quality_checks.find((c) => c.id === 'duplication_rate')?.status} />
          </div>

          {cov && (
            <ChartCard
              title={`Coverage of known pathogenic sites (≥ ${cov.min_depth}×)`}
              subtitle={`${fmtInt(cov.covered)} / ${fmtInt(cov.sites)} ClinVar ${aln.clinvar_version ?? ''} P/LP sites covered (${fmtPct(cov.fraction_covered)}), median depth ${fmtNum(cov.median_depth, 0)}×. An uncovered site cannot be excluded.`}
              table={{
                columns: ['Gene', 'P/LP sites', 'Covered', 'Fraction', 'Median DP', 'Sites at 0×'],
                rows: cov.per_gene.map((g) => [g.gene, g.sites, g.covered, fmtPct(g.fraction_covered), fmtNum(g.median_depth, 0), g.zero_depth_sites]),
              }}
            >
              <BarList
                items={cov.per_gene.map((g) => ({
                  label: g.gene,
                  value: g.fraction_covered ?? 0,
                  display: `${fmtPct(g.fraction_covered)}  (${g.covered}/${g.sites})`,
                  color: (g.fraction_covered ?? 0) >= 0.95 ? 'var(--viz-seq)' : 'var(--viz-series-2)',
                  flag: (g.fraction_covered ?? 0) < 0.95 ? <AlertTriangle className="h-3.5 w-3.5 shrink-0" style={{ color: 'var(--viz-warning)' }} aria-label="below threshold" /> : undefined,
                  tooltip: [`${g.covered} / ${g.sites} sites ≥ ${cov.min_depth}×`, `median depth ${fmtNum(g.median_depth, 0)}×`, `${g.zero_depth_sites} site(s) with no read`],
                }))}
                max={1}
                threshold={{ value: 0.95, label: 'target: 95% of sites' }}
                labelWidth="4.5rem"
                valueColumn="8.5rem"
                ariaLabel="Fraction of pathogenic sites covered per gene"
              />
              <p className="mt-3 text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">{cov.definition}</p>
            </ChartCard>
          )}
        </>
      ) : (
        <p className="rounded-xl border border-dashed border-slate-300 p-4 text-xs text-slate-500 dark:border-slate-700">
          VCF provided directly: no BAM, hence no alignment or coverage metrics. Run the analysis from FASTQ files to
          measure coverage of known pathogenic sites.
        </p>
      )}
    </div>
  );
}
