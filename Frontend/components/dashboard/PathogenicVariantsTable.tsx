import { AlertTriangle } from 'lucide-react';
import type { PathogenicVariant } from '@/types/api';

interface PathogenicVariantsTableProps {
  variants: PathogenicVariant[];
  genes?: string[];
  title?: string;
  emptyMessage?: string;
  /** Variants « à confirmer » : affiche le motif (contrôle qualité ou note) */
  showQcReason?: boolean;
}

const ZYGOSITY_FR: Record<string, string> = {
  heterozygous: 'hétéro.',
  homozygous: 'homo.',
  hemizygous: 'hémi.',
};

export default function PathogenicVariantsTable({
  variants,
  genes,
  title = 'Variants pathogènes confirmés',
  emptyMessage = 'Aucun variant pathogène ou probablement pathogène (ClinVar) sur les gènes germinaux du panel.',
  showQcReason = false,
}: PathogenicVariantsTableProps) {
  return (
    <section className="rounded-2xl border border-slate-200 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900 dark:shadow-card-dark overflow-hidden">
      <div className="border-b border-slate-200 px-6 py-4 dark:border-slate-700">
        <h2 className="text-lg font-bold text-slate-900 dark:text-white">
          {title}
        </h2>
        {genes && genes.length > 0 && (
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
            Gènes identifiés :{' '}
            <span className="font-mono font-semibold text-dna-700 dark:text-dna-400">
              {genes.join(', ')}
            </span>
          </p>
        )}
      </div>

      {variants.length === 0 ? (
        <div className="flex items-center gap-3 px-6 py-10 text-slate-500">
          <AlertTriangle className="h-5 w-5 shrink-0 text-clinical-medium" />
          <p className="text-sm">{emptyMessage}</p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:border-slate-700 dark:bg-slate-800/80">
                <th className="px-6 py-3">Gène</th>
                <th className="px-4 py-3">Mutation</th>
                <th className="px-4 py-3">Locus</th>
                <th className="px-4 py-3 text-right font-mono">QUAL</th>
                <th className="px-4 py-3 text-right font-mono">DP</th>
                <th className="px-4 py-3 text-right font-mono">VAF</th>
                <th className="px-4 py-3">Zygotie</th>
                <th className="px-4 py-3">ClinVar</th>
                {showQcReason && <th className="px-4 py-3">Motif</th>}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {variants.map((v, i) => (
                <tr
                  key={`${v.gene}-${v.position}-${i}`}
                  className="transition hover:bg-dna-500/5"
                >
                  <td className="px-6 py-3.5 font-semibold text-slate-900 dark:text-white">
                    {v.gene}
                  </td>
                  <td className="px-4 py-3.5 font-mono text-xs text-slate-800 dark:text-slate-200">
                    {v.mutation}
                    {v.rsid && (
                      <span className="ml-2 text-slate-500">{v.rsid}</span>
                    )}
                  </td>
                  <td className="px-4 py-3.5 font-mono text-xs text-slate-600 dark:text-slate-400">
                    {v.chromosome}:{v.position}
                  </td>
                  <td className="px-4 py-3.5 text-right font-mono text-sm tabular-nums text-slate-900 dark:text-white">
                    {fmtNum(v.gatk_metrics?.QUAL)}
                  </td>
                  <td className="px-4 py-3.5 text-right font-mono text-sm tabular-nums text-slate-900 dark:text-white">
                    {fmtInt(v.gatk_metrics?.DP)}
                  </td>
                  <td className="px-4 py-3.5 text-right font-mono text-sm tabular-nums text-dna-700 dark:text-dna-400">
                    {fmtVaf(v.gatk_metrics?.VAF)}
                  </td>
                  <td className="px-4 py-3.5 text-xs text-slate-700 dark:text-slate-300">
                    {ZYGOSITY_FR[v.zygosity ?? ''] ?? '—'}
                  </td>
                  <td className="px-4 py-3.5">
                    <span className="rounded-md border border-clinical-high/30 bg-clinical-high/10 px-2 py-0.5 text-xs font-semibold text-clinical-high">
                      {(v.pathogenicity ?? 'Non classé').replace(/_/g, ' ')}
                    </span>
                    {v.review_stars != null && (
                      <span
                        className="ml-2 text-xs text-amber-500"
                        title={v.review_status ?? undefined}
                      >
                        {'★'.repeat(v.review_stars)}
                        {'☆'.repeat(Math.max(0, 4 - v.review_stars))}
                      </span>
                    )}
                  </td>
                  {showQcReason && (
                    <td className="px-4 py-3.5 text-xs text-slate-600 dark:text-slate-400">
                      {v.note ?? v.qc_flags?.join(', ') ?? '—'}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function fmtNum(v: number | null | undefined): string {
  if (v == null) return '—';
  return Number.isInteger(v) ? String(v) : v.toFixed(2);
}

function fmtInt(v: number | null | undefined): string {
  if (v == null) return '—';
  return String(v);
}

function fmtVaf(v: number | null | undefined): string {
  if (v == null) return '—';
  return v < 1 ? `${(v * 100).toFixed(1)}%` : `${v.toFixed(1)}%`;
}
