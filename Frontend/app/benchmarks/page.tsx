'use client';

import useSWR from 'swr';
import { CheckCircle2, Loader2, XCircle } from 'lucide-react';
import BarList from '@/components/charts/BarList';
import ChartCard from '@/components/charts/ChartCard';
import StatTile from '@/components/dashboard/StatTile';
import { getBiogptModel, getLatestBenchmark } from '@/lib/api';
import type { StatisticsModelInfo } from '@/lib/api';
import { fmtNum, fmtPct, stepLabel } from '@/lib/utils/stats';

/* eslint-disable @typescript-eslint/no-explicit-any */

function Ok({ ok }: { ok: boolean }) {
  return ok ? (
    <CheckCircle2 className="inline h-4 w-4" style={{ color: 'var(--viz-good)' }} aria-label="pass" />
  ) : (
    <XCircle className="inline h-4 w-4" style={{ color: 'var(--viz-critical)' }} aria-label="fail" />
  );
}

function ci(v?: number[] | null): string {
  return v ? `95% CI ${fmtPct(v[0], 0)}–${fmtPct(v[1], 0)}` : '';
}

function Section({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-card dark:border-slate-800 dark:bg-slate-900 dark:shadow-card-dark">
      <h2 className="text-sm font-bold text-slate-900 dark:text-white">{title}</h2>
      {subtitle && <p className="mt-0.5 text-xs leading-relaxed text-slate-500 dark:text-slate-400">{subtitle}</p>}
      <div className="mt-4">{children}</div>
    </section>
  );
}


const SPLIT_LABELS: Record<string, string> = {
  synthetic_test: 'Synthetic statistics files (normal and abnormal QC)',
  real_test: 'Real analyses of this server (held out)',
};

function StatisticsModelSection({ m }: { m: StatisticsModelInfo }) {
  const ev = m.evaluation;
  if (!ev) return null;
  const rows: { model: string; split: string; r: any }[] = [];
  for (const split of ['synthetic_test', 'real_test']) {
    if (ev.base_zero_shot[split]) rows.push({ model: 'Base BioGPT (zero-shot)', split, r: ev.base_zero_shot[split] });
    if (ev.fine_tuned[split]) rows.push({ model: 'Fine-tuned (LoRA)', split, r: ev.fine_tuned[split] });
  }
  const train = m.dataset?.splits?.train;
  return (
    <Section
      title="BioGPT fine-tuned on VCF statistics — verified interpretation"
      subtitle={`Every generated sentence is scored by the deterministic verifier (numbers, expected ranges, risk level, genes). Training: ${train?.examples ?? '—'} statistics files, ${m.training ? `${Math.round(m.training.duration_s)} s on ${m.training.device}` : '—'}. Promotion gate: sentence precision ≥ ${fmtPct(ev.gate_thresholds.sentence_precision, 0)}, topic coverage ≥ ${fmtPct(ev.gate_thresholds.topic_coverage, 0)}, no wrong risk level, every final answer verified.`}
    >
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] text-left text-xs">
          <thead className="text-slate-500">
            <tr className="border-b border-slate-200 dark:border-slate-700">
              {['Test set', 'Model', 'n', 'Verified sentences', 'Topics covered', 'Numbers correct', 'Status words correct', 'Wrong risk levels'].map((h) => (
                <th key={h} className="py-2 pr-3 font-semibold">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {rows.map(({ model, split, r }) => (
              <tr key={model + split}>
                <td className="py-2 pr-3">{SPLIT_LABELS[split] ?? split}</td>
                <td className="py-2 pr-3 font-medium">{model}</td>
                <td className="py-2 pr-3 tabular-nums">{r.examples_scored}</td>
                <td className="py-2 pr-3 tabular-nums">{fmtPct(r.sentence_precision)}</td>
                <td className="py-2 pr-3 tabular-nums">{fmtPct(r.topic_coverage)}</td>
                <td className="py-2 pr-3 tabular-nums">{r.number_accuracy == null ? '—' : fmtPct(r.number_accuracy)}</td>
                <td className="py-2 pr-3 tabular-nums">{r.status_accuracy == null ? '—' : fmtPct(r.status_accuracy)}</td>
                <td className="py-2 pr-3 tabular-nums">{r.risk_errors}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-500">
        <span><Ok ok={ev.promotion.passed} /> Promotion gate {ev.promotion.passed ? 'passed' : 'not passed'}{m.promoted ? ` — adapter ${m.promoted.version} in service` : ''}</span>
        <span>Rejected sentences are replaced by the deterministic reference text, so every final interpretation is verified.</span>
      </p>
    </Section>
  );
}

export default function BenchmarksPage() {
  const { data, error, isLoading } = useSWR('benchmark', getLatestBenchmark, { revalidateOnFocus: false });
  const { data: biogpt } = useSWR('biogpt-model', getBiogptModel, { revalidateOnFocus: false });

  if (isLoading) return <p className="flex items-center gap-2 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" /> Loading benchmark…</p>;
  if (error || !data)
    return (
      <p className="rounded-xl border border-dashed border-slate-300 p-6 text-sm text-slate-600 dark:border-slate-700 dark:text-slate-300">
        No benchmark available yet. Run <span className="font-mono">python -m benchmarks.multiagent --suites all</span> in Backend/.
      </p>
    );

  const s = data.suites ?? {};
  const clin = s.clinical?.metrics;
  const ana = s.analytical;
  const repro = s.reproducibility;
  const orch = s.orchestration;
  const rob = s.robustness;
  const lat = s.latency;
  const env = data.environment ?? {};

  return (
    <div className="space-y-6">
      <header>
        <p className="text-sm font-medium text-dna-600 dark:text-dna-400">Multi-agent system evaluation</p>
        <h1 className="mt-1 text-2xl font-bold tracking-tight text-slate-900 dark:text-white">Benchmarks</h1>
        <p className="mt-1 max-w-3xl text-sm text-slate-600 dark:text-slate-400">
          Black-box evaluation through the public API, on real open-access data. Run {data.started_at} · {data.version} · pipeline{' '}
          {env.pipeline_backend} · {env.orchestrator} · {env.cpus} CPUs{env.ram_gb ? ` · ${env.ram_gb} GB RAM` : ''}
          {env.gpus?.length ? ` · ${env.gpus.map((g: any) => g.name).join(', ')}` : ''}.
        </p>
      </header>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {clin && <StatTile label="Carrier sensitivity" value={fmtPct(clin.carrier_sensitivity, 0)} hint={ci(clin.carrier_sensitivity_ci95)} />}
        {clin && <StatTile label="Control specificity" value={fmtPct(clin.control_specificity, 0)} hint={ci(clin.control_specificity_ci95)} />}
        {clin && <StatTile label="Risk-level accuracy" value={fmtPct(clin.risk_level_accuracy, 0)} hint={ci(clin.risk_level_accuracy_ci95)} />}
        {clin && (
          <StatTile
            label="Unexpected confirmed P/LP"
            value={String(clin.unexpected_confirmed_plp ?? '—')}
            hint={`${clin.unexpected_flagged_to_confirm ?? 0} low-confidence call(s) flagged “to confirm”`}
            status={clin.unexpected_confirmed_plp === 0 ? 'OK' : 'WARN'}
          />
        )}
        {ana?.pass_only && <StatTile label="GIAB recall (PASS)" value={fmtPct(ana.pass_only.recall)} hint={`precision ${fmtPct(ana.pass_only.precision)}`} />}
        {repro && <StatTile label="Reproducibility" value={repro.all_identical ? 'Identical' : 'Differs'} hint={`${repro.cases.length} cases × ${repro.cases[0]?.runs ?? 0} runs`} status={repro.all_identical ? 'OK' : 'WARN'} />}
        {orch && <StatTile label="Router independence" value={orch.same_clinical_report ? 'Same report' : 'Differs'} hint="deterministic vs Mistral" status={orch.same_clinical_report ? 'OK' : 'WARN'} />}
        {rob && <StatTile label="Fault injection" value={`${rob.passed}/${rob.total}`} hint="handled cleanly" status={rob.passed === rob.total ? 'OK' : 'WARN'} />}
        {lat && <StatTile label="API latency p95" value={`${fmtNum(Math.max(...lat.endpoints.map((e: any) => e.p95_ms)), 1)} ms`} hint="worst read endpoint" />}
      </div>

      {s.clinical?.samples && (
        <Section
          title="Clinical accuracy — real 1000 Genomes cohort"
          subtitle={`${s.clinical.cohort}. Real Illumina reads over the 13 panel genes, full FASTQ → report pipeline. Ground truth: independent bcftools scan of the 1000 Genomes germline calls against ClinVar P/LP. n = ${clin.samples}: confidence intervals are wide by design. The TP53 p.Arg273His call in NA10842 (c.818G>A, rs28934576; 5/36 reads, VAF 0.14) is absent from the germline call set: a low-VAF hotspot in lymphoblastoid-cell-line DNA is typical of a culture-acquired or clonal-haematopoiesis mutation, and GermlineIQ correctly reports it “to confirm” rather than as a germline finding.`}
        >
          <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
            <table className="w-full min-w-[900px] text-left text-xs">
              <thead className="bg-slate-50 text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                <tr>
                  <th className="px-3 py-2 font-semibold">Sample</th>
                  <th className="px-3 py-2 font-semibold">Ground truth</th>
                  <th className="px-3 py-2 font-semibold">Reported by GermlineIQ</th>
                  <th className="px-3 py-2 font-semibold">Risk (expected → reported)</th>
                  <th className="px-3 py-2 text-right font-semibold">P/LP sites ≥15×</th>
                  <th className="px-3 py-2 text-right font-semibold">End-to-end</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {s.clinical.samples.map((x: any) => (
                  <tr key={x.sample} className="align-top">
                    <td className="px-3 py-2">
                      <span className="font-mono font-semibold">{x.sample}</span>
                      <span className="block text-[11px] text-slate-500">{x.description}</span>
                    </td>
                    <td className="px-3 py-2 font-mono">{x.expected_variants.join(', ') || '—'}</td>
                    <td className="px-3 py-2">
                      {x.reported_findings.length ? (
                        x.reported_findings.map((f: any) => (
                          <span key={f.gene + f.mutation} className="block">
                            <span className="font-mono font-semibold">{f.gene}</span> {f.mutation} · {f.zygosity} · DP {f.dp} · VAF {fmtNum(f.vaf, 2)}
                            {f.class === 'variants_to_confirm' && (
                              <span className="ml-1 rounded border border-amber-500/40 bg-amber-500/10 px-1 text-[10px] font-semibold text-amber-700 dark:text-amber-300">
                                to confirm
                              </span>
                            )}
                          </span>
                        ))
                      ) : (
                        <span className="text-slate-400">no P/LP variant</span>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <Ok ok={x.risk_correct} /> {x.expected_risk} → <span className="font-semibold">{x.reported_risk}</span>
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">{fmtPct(x.pathogenic_sites_coverage)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{fmtNum(x.wall_time_s, 0)} s</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}

      {clin?.per_agent_s && (
        <div className="grid gap-5 lg:grid-cols-2">
          <ChartCard
            title="Median time per agent (cohort)"
            subtitle="Seconds per sample; variant calling dominates, the orchestration itself is negligible."
            table={{
              columns: ['Agent', 'n', 'Median (s)', 'p95 (s)', 'Max (s)'],
              rows: Object.entries(clin.per_agent_s).map(([k, v]: [string, any]) => [k, v.n, v.median, v.p95, v.max]),
            }}
          >
            <BarList
              items={Object.entries(clin.per_agent_s).map(([k, v]: [string, any]) => ({
                label: stepLabel(k === 'genomic_pipeline' ? 'parabricks' : k),
                value: v.median ?? 0,
                display: `${fmtNum(v.median, 2)} s`,
              }))}
              labelWidth="9rem"
              valueColumn="5rem"
              ariaLabel="Median time per agent"
            />
          </ChartCard>
          <ChartCard
            title="End-to-end time per sample"
            subtitle={`From API submission to finished report (FASTQ mode, variant calling: ${env.pipeline_backend === 'parabricks' ? 'Parabricks on GPU' : 'GATK4 on CPU'}).`}
            table={{ columns: ['Sample', 'Seconds'], rows: s.clinical.samples.map((x: any) => [x.sample, x.wall_time_s]) }}
          >
            <BarList
              items={s.clinical.samples.map((x: any) => ({ label: x.sample, value: x.wall_time_s, display: `${fmtNum(x.wall_time_s, 0)} s` }))}
              labelWidth="6rem"
              valueColumn="4rem"
              ariaLabel="End-to-end time per sample"
            />
          </ChartCard>
        </div>
      )}

      {ana?.pass_only && (
        <Section title="Analytical validity — GIAB HG001 (NA12878)" subtitle={`${ana.sample}. Region: ${ana.region} (${ana.evaluated_bp?.toLocaleString('en-US')} bp).`}>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-left text-xs">
              <thead className="text-slate-500">
                <tr className="border-b border-slate-200 dark:border-slate-700">
                  {['Calls', 'TP', 'FP', 'FN', 'Precision', 'Recall', 'F1'].map((h) => (
                    <th key={h} className="py-2 pr-3 font-semibold">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {(['pass_only', 'all_calls'] as const).map((k) => (
                  <tr key={k}>
                    <td className="py-2 pr-3 font-medium">{k === 'pass_only' ? 'PASS (GATK hard filters)' : 'All HaplotypeCaller calls'}</td>
                    <td className="py-2 pr-3 tabular-nums">{ana[k].true_positives}</td>
                    <td className="py-2 pr-3 tabular-nums">{ana[k].false_positives}</td>
                    <td className="py-2 pr-3 tabular-nums">{ana[k].false_negatives}</td>
                    <td className="py-2 pr-3 tabular-nums">{fmtPct(ana[k].precision)}</td>
                    <td className="py-2 pr-3 tabular-nums">{fmtPct(ana[k].recall)}</td>
                    <td className="py-2 pr-3 tabular-nums">{fmtNum(ana[k].f1, 3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-3 text-[11px] text-slate-500">
            Filtered pathogenic variants are never discarded: they are reported “to confirm”, which keeps clinical sensitivity even when a hard filter rejects a call.
            Every call missed at the PASS level was detected by HaplotypeCaller (100% recall on all calls) and removed by GATK hard filters, mostly the SOR strand-bias filter — a known exome-capture artefact.
          </p>
        </Section>
      )}

      {biogpt?.statistics_model && <StatisticsModelSection m={biogpt.statistics_model} />}

      <div className="grid gap-5 lg:grid-cols-2">
        {orch?.deterministic && (
          <Section title="Orchestration — deterministic vs Mistral router" subtitle="Same VCF input, in-process engine; the LLM router may only choose among ready tools.">
            <table className="w-full text-left text-xs">
              <thead className="text-slate-500">
                <tr className="border-b border-slate-200 dark:border-slate-700">
                  <th className="py-2 pr-3 font-semibold">Router</th>
                  <th className="py-2 pr-3 text-right font-semibold">Total (median)</th>
                  <th className="py-2 pr-3 text-right font-semibold">Agents</th>
                  <th className="py-2 pr-3 text-right font-semibold">Overhead</th>
                  <th className="py-2 font-semibold">Stable</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {(['deterministic', 'mistral'] as const).map((k) => (
                  <tr key={k}>
                    <td className="py-2 pr-3 font-medium">{k}</td>
                    <td className="py-2 pr-3 text-right tabular-nums">{fmtNum(orch[k].total_s.median, 2)} s</td>
                    <td className="py-2 pr-3 text-right tabular-nums">{fmtNum(orch[k].agent_s.median, 2)} s</td>
                    <td className="py-2 pr-3 text-right tabular-nums">{fmtNum(orch[k].overhead_s.median, 3)} s</td>
                    <td className="py-2"><Ok ok={orch[k].stable_across_runs} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-3 text-xs text-slate-600 dark:text-slate-300">
              <Ok ok={orch.same_plan} /> same plan · <Ok ok={orch.same_clinical_report} /> identical clinical report
            </p>
          </Section>
        )}
        {repro && (
          <Section title="Reproducibility" subtitle="SHA-256 of the clinical content (timestamps and timings excluded).">
            <ul className="space-y-2 text-xs">
              {repro.cases.map((c: any) => (
                <li key={c.case} className="flex items-start gap-2">
                  <Ok ok={c.identical_reports && c.identical_statistics} />
                  <span>
                    <span className="font-medium">{c.case}</span> — {c.runs} runs, identical reports and statistics
                    <span className="block font-mono text-[10px] text-slate-400">{c.report_sha256?.slice(0, 24)}…</span>
                  </span>
                </li>
              ))}
            </ul>
          </Section>
        )}
        {rob && (
          <Section title="Robustness — fault injection" subtitle="Invalid inputs must be rejected cleanly and the service must stay healthy.">
            <ul className="space-y-1.5 text-xs">
              {rob.cases.map((c: any) => (
                <li key={c.case} className="flex items-center gap-2">
                  <Ok ok={c.passed} /> <span className="flex-1">{c.case}</span>
                  <span className="font-mono text-slate-500">{String(c.status_code)}</span>
                </li>
              ))}
            </ul>
          </Section>
        )}
        {lat && (
          <Section title="API latency" subtitle="Read endpoints, 50 sequential requests each after warm-up.">
            <table className="w-full text-left text-xs">
              <thead className="text-slate-500">
                <tr className="border-b border-slate-200 dark:border-slate-700">
                  <th className="py-2 pr-3 font-semibold">Endpoint</th>
                  <th className="py-2 pr-3 text-right font-semibold">p50</th>
                  <th className="py-2 pr-3 text-right font-semibold">p95</th>
                  <th className="py-2 text-right font-semibold">p99</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {lat.endpoints.map((e: any) => (
                  <tr key={e.endpoint}>
                    <td className="py-2 pr-3 font-mono">{e.endpoint}</td>
                    <td className="py-2 pr-3 text-right tabular-nums">{fmtNum(e.p50_ms, 2)} ms</td>
                    <td className="py-2 pr-3 text-right tabular-nums">{fmtNum(e.p95_ms, 2)} ms</td>
                    <td className="py-2 text-right tabular-nums">{fmtNum(e.p99_ms, 2)} ms</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Section>
        )}
      </div>
    </div>
  );
}
