'use client';

import useSWR from 'swr';
import { BadgeCheck, BookOpen, Fingerprint, ShieldCheck, XCircle } from 'lucide-react';
import SystemMetricsCard from '@/components/dashboard/SystemMetricsCard';
import { getBiogptModel } from '@/lib/api';
import { fmtInt, fmtNum, fmtPct } from '@/lib/utils/stats';
import type { ClinicalReport } from '@/types/api';

function Card({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-card dark:border-slate-800 dark:bg-slate-900 dark:shadow-card-dark">
      <h3 className="mb-3 flex items-center gap-2 text-sm font-bold text-slate-900 dark:text-white">
        {icon}
        {title}
      </h3>
      {children}
    </section>
  );
}

export default function AiTracePanel({ report }: { report: ClinicalReport }) {
  const pred = report.clinical_prediction;
  const ver = pred.commentary_verification;
  const rep = report.reproducibility;
  const { data: model } = useSWR('biogpt-model', getBiogptModel, { revalidateOnFocus: false });

  return (
    <div className="space-y-5">
      <Card title="BioGPT literature commentary — verified" icon={<ShieldCheck className="h-4 w-4 text-dna-500" />}>
        <p className="mb-3 text-xs leading-relaxed text-slate-500 dark:text-slate-400">
          BioGPT never decides the risk. Every generated sentence is checked against the reference gene–disease associations
          (ClinGen/NCCN); an unsupported claim is replaced by a deterministic reference sentence.
        </p>
        {ver ? (
          <>
            <p className="mb-3 text-xs text-slate-600 dark:text-slate-300">
              {ver.verified}/{ver.generated} generated sentence(s) verified · model <span className="font-mono">{ver.model}</span>
              {ver.adapter ? ' + LoRA adapter' : ' (base)'} · knowledge <span className="font-mono">{ver.knowledge_version}</span>
            </p>
            <ul className="space-y-3">
              {ver.per_gene.map((g) => (
                <li key={g.gene} className="rounded-xl border border-slate-200 p-3 dark:border-slate-700">
                  <div className="mb-1.5 flex items-center gap-2 text-xs">
                    <span className="font-mono font-bold">{g.gene}</span>
                    {g.verified ? (
                      <span className="inline-flex items-center gap-1 font-semibold text-slate-700 dark:text-slate-200">
                        <BadgeCheck className="h-3.5 w-3.5" style={{ color: 'var(--viz-good)' }} aria-hidden /> BioGPT text verified
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 font-semibold text-slate-700 dark:text-slate-200">
                        <XCircle className="h-3.5 w-3.5" style={{ color: 'var(--viz-critical)' }} aria-hidden /> rejected — reference sentence used
                      </span>
                    )}
                  </div>
                  <p className="text-sm leading-relaxed text-slate-800 dark:text-slate-100">{g.final_text}</p>
                  {!g.verified && g.text && (
                    <p className="mt-1.5 text-[11px] text-slate-500">
                      Discarded generated text: “{g.text}” — reason: {g.reason}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="text-xs text-slate-500">
            No commentary: BioGPT is only called for genes carrying a pathogenic or to-be-confirmed variant.
          </p>
        )}
      </Card>

      {model?.fine_tuning && (
        <Card title="BioGPT LoRA fine-tuning" icon={<BookOpen className="h-4 w-4 text-dna-500" />}>
          <p className="mb-3 text-xs leading-relaxed text-slate-500 dark:text-slate-400">
            Frozen corpus: {fmtInt(model.corpus?.abstracts_kept)} PubMed abstracts ({model.corpus?.date_range?.join(' → ')}) collected on{' '}
            {model.corpus?.retrieved_at?.slice(0, 10)}, SHA-256 <span className="font-mono">{model.corpus?.sha256?.slice(0, 12)}…</span>
            {model.last_training && (
              <> · {fmtInt(model.last_training.trainable_parameters)} trained parameters out of {fmtInt(model.last_training.total_parameters)} ({model.last_training.device})</>
            )}
          </p>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-xs">
              <thead className="text-slate-500">
                <tr className="border-b border-slate-200 dark:border-slate-700">
                  <th className="py-2 pr-3 font-semibold">Iteration</th>
                  <th className="py-2 pr-3 text-right font-semibold">Test perplexity</th>
                  <th className="py-2 pr-3 text-right font-semibold">Production prompt: verified</th>
                  <th className="py-2 pr-3 text-right font-semibold">Held-out prompts: unsupported</th>
                  <th className="py-2 font-semibold">Promotion</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {model.fine_tuning.iterations.map((it) => (
                  <tr key={it.iteration} className="align-top">
                    <td className="py-2 pr-3">
                      <span className="font-mono font-semibold">{it.iteration}</span>
                      <span className="block text-[11px] text-slate-500">{it.description}</span>
                    </td>
                    <td className="py-2 pr-3 text-right tabular-nums">{fmtNum(it.base.test_perplexity)} → {fmtNum(it.fine_tuned.test_perplexity)}</td>
                    <td className="py-2 pr-3 text-right tabular-nums">{fmtPct(it.base.in_distribution_verified_rate, 0)} → {fmtPct(it.fine_tuned.in_distribution_verified_rate, 0)}</td>
                    <td className="py-2 pr-3 text-right tabular-nums">{fmtPct(it.base.held_out_unsupported_claim_rate, 0)} → {fmtPct(it.fine_tuned.held_out_unsupported_claim_rate, 0)}</td>
                    <td className="py-2">{it.accepted ? 'promoted' : 'rejected'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-3 text-xs text-slate-600 dark:text-slate-300">
            <span className="font-semibold">Decision:</span> {model.fine_tuning.decision}. In service: {model.adapter_in_service ? 'LoRA adapter' : model.fine_tuning.production_default}.
          </p>
          <p className="mt-1 text-[11px] text-slate-500">{model.fine_tuning.statistical_note}</p>
        </Card>
      )}

      <Card title="Reproducibility" icon={<Fingerprint className="h-4 w-4 text-dna-500" />}>
        <dl className="grid gap-x-6 gap-y-2 text-xs sm:grid-cols-2">
          {[
            ['VCF fingerprint (SHA-256)', rep?.input_sha256],
            ['Panel version', rep?.panel_version],
            ['Annotation', `${rep?.annotation_source ?? '—'} ${rep?.annotation_version ?? ''}`],
            ['Decision method', rep?.decision_method],
            ['Statistics', report.statistics?.version],
            ['Software', rep?.software_version],
          ].map(([k, v]) => (
            <div key={k} className="flex flex-col">
              <dt className="text-slate-500">{k}</dt>
              <dd className="break-all font-mono text-slate-800 dark:text-slate-100">{v ?? '—'}</dd>
            </div>
          ))}
        </dl>
        {rep?.qc_thresholds && (
          <p className="mt-3 text-[11px] text-slate-500">
            Clinical thresholds: {Object.entries(rep.qc_thresholds).map(([k, v]) => `${k} = ${v}`).join(' · ')}
          </p>
        )}
      </Card>

      <SystemMetricsCard
        metrics={report.system_metrics}
        patientId={report.patient_id}
        reportId={report.report_id}
        generatedAt={report.generated_at}
      />
    </div>
  );
}
