'use client';

import { useState } from 'react';
import { BadgeCheck, BookText, ChevronDown, ShieldCheck, Sparkles, XCircle } from 'lucide-react';
import type { StatisticsInterpretation } from '@/types/api';

const TOPIC_LABELS: Record<string, string> = {
  overview: 'Variant calls',
  pass_rate: 'GATK filters',
  ti_tv: 'Ti/Tv',
  het_hom: 'Het/hom',
  het_vaf: 'Heterozygous VAF',
  median_depth: 'Depth',
  mapped_rate: 'Mapping',
  duplication_rate: 'Duplicates',
  site_coverage: 'Pathogenic-site coverage',
  not_excluded: 'Not excluded',
  confirmed: 'Confirmed P/LP',
  to_confirm: 'To confirm',
  low_vaf: 'Low-VAF calls',
  risk: 'Risk level',
};

function SourceBadge({ source }: { source: 'model' | 'reference' }) {
  return source === 'model' ? (
    <span className="inline-flex items-center gap-1 rounded-md border border-slate-200 px-1.5 py-0.5 text-[10px] font-semibold text-slate-600 dark:border-slate-700 dark:text-slate-300">
      <Sparkles className="h-3 w-3" style={{ color: 'var(--viz-good)' }} aria-hidden /> BioGPT · verified
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded-md border border-slate-200 px-1.5 py-0.5 text-[10px] font-semibold text-slate-500 dark:border-slate-700 dark:text-slate-400">
      <BookText className="h-3 w-3" aria-hidden /> Reference text
    </span>
  );
}

export default function StatsInterpretationCard({ interpretation }: { interpretation: StatisticsInterpretation }) {
  const [showDetails, setShowDetails] = useState(false);
  const m = interpretation.metrics;
  const fromModel = interpretation.source === 'biogpt-stats';
  const rejected = interpretation.sentences.filter((s) => !s.verified);

  // One row per emitted sentence (a sentence may answer several topics)
  const rows: { sentence: string; source: 'model' | 'reference'; topics: string[] }[] = [];
  for (const p of interpretation.per_topic) {
    const existing = rows.find((r) => r.sentence === p.sentence);
    if (existing) existing.topics.push(p.topic);
    else rows.push({ sentence: p.sentence, source: p.source, topics: [p.topic] });
  }

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-card dark:border-slate-800 dark:bg-slate-900 dark:shadow-card-dark">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 text-sm font-bold text-slate-900 dark:text-white">
            <ShieldCheck className="h-4 w-4 text-dna-500" aria-hidden />
            Interpretation of the VCF statistics
          </h3>
          <p className="mt-1 max-w-3xl text-xs leading-relaxed text-slate-500 dark:text-slate-400">
            {fromModel
              ? 'Written by BioGPT fine-tuned on VCF statistics. Every sentence was checked against the computed statistics (numbers, expected ranges, risk level, genes); topics without a verified sentence use the deterministic reference text.'
              : `Deterministic reference text${interpretation.note ? ` — ${interpretation.note}` : ''}.`}
          </p>
        </div>
        <span
          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-2.5 py-1 text-xs font-semibold text-slate-700 dark:border-slate-700 dark:text-slate-200"
          title="Every sentence of the final text passed the deterministic verifier"
        >
          {interpretation.final_verified ? (
            <BadgeCheck className="h-3.5 w-3.5" style={{ color: 'var(--viz-good)' }} aria-hidden />
          ) : (
            <XCircle className="h-3.5 w-3.5" style={{ color: 'var(--viz-critical)' }} aria-hidden />
          )}
          Final text {interpretation.final_verified ? 'verified' : 'NOT verified'}
        </span>
      </div>

      {fromModel && (
        <dl className="mb-4 grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
          {[
            ['Generated sentences', m.generated_sentences],
            ['Verified', m.verified_sentences],
            ['Rejected', m.rejected_sentences],
            ['Topics from the model', `${m.topics_from_model}/${m.required_topics}`],
          ].map(([label, value]) => (
            <div key={label as string} className="rounded-lg border border-slate-200 px-3 py-2 dark:border-slate-700">
              <dt className="text-slate-500 dark:text-slate-400">{label}</dt>
              <dd className="mt-0.5 text-base font-bold tabular-nums text-slate-900 dark:text-white">{value}</dd>
            </div>
          ))}
        </dl>
      )}

      <ol className="space-y-2">
        {rows.map((r) => (
          <li key={r.sentence} className="flex flex-col gap-1 rounded-lg bg-slate-50 px-3 py-2 dark:bg-slate-800/60 sm:flex-row sm:items-start sm:gap-3">
            <div className="flex shrink-0 flex-wrap items-center gap-1.5 sm:w-56">
              <SourceBadge source={r.source} />
              <span className="text-[10px] font-medium uppercase tracking-wide text-slate-400">
                {r.topics.map((t) => TOPIC_LABELS[t] ?? t).join(' · ')}
              </span>
            </div>
            <p className="text-sm leading-relaxed text-slate-800 dark:text-slate-100">{r.sentence}</p>
          </li>
        ))}
      </ol>

      {fromModel && (
        <div className="mt-4">
          <button
            type="button"
            onClick={() => setShowDetails((v) => !v)}
            aria-expanded={showDetails}
            className="inline-flex items-center gap-1.5 text-xs font-semibold text-dna-600 hover:underline dark:text-dna-400"
          >
            <ChevronDown className={`h-3.5 w-3.5 transition ${showDetails ? 'rotate-180' : ''}`} aria-hidden />
            Verification details ({rejected.length} rejected sentence{rejected.length === 1 ? '' : 's'})
          </button>
          {showDetails && (
            <div className="mt-3 space-y-3">
              {rejected.length > 0 && (
                <ul className="space-y-1.5">
                  {rejected.map((s) => (
                    <li key={s.sentence} className="rounded-lg border border-slate-200 px-3 py-2 text-xs dark:border-slate-700">
                      <p className="flex items-start gap-1.5 text-slate-700 dark:text-slate-200">
                        <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" style={{ color: 'var(--viz-critical)' }} aria-hidden />
                        <span className="line-through decoration-slate-400">{s.sentence}</span>
                      </p>
                      <p className="mt-1 pl-5 text-slate-500 dark:text-slate-400">{s.reasons.join(' · ')}</p>
                    </li>
                  ))}
                </ul>
              )}
              {interpretation.model_output && (
                <details className="text-xs">
                  <summary className="cursor-pointer font-semibold text-slate-600 dark:text-slate-300">Raw model output</summary>
                  <p className="mt-2 whitespace-pre-wrap rounded-lg bg-slate-50 p-3 font-mono text-[11px] leading-relaxed text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                    {interpretation.model_output}
                  </p>
                </details>
              )}
              <p className="text-[11px] text-slate-400">
                {Object.entries(interpretation.versions).map(([k, v]) => `${k}: ${v}`).join(' · ')}
              </p>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
