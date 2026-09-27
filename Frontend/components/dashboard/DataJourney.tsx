import { ChevronRight } from 'lucide-react';
import { fmtCompact, fmtInt, fmtPct } from '@/lib/utils/stats';
import type { ClinicalReport } from '@/types/api';

interface Stage {
  agent: string;
  title: string;
  value: string;
  detail: string;
}

/** End-to-end data trail: what each agent received and produced. */
export default function DataJourney({ report }: { report: ClinicalReport }) {
  const st = report.statistics;
  const aln = st?.alignment;
  const panel = st?.panel;
  const cats = panel?.by_category;
  const plp = cats ? cats.pathogenic_confirmed + cats.pathogenic_to_confirm + cats.lof_to_confirm : 0;

  const stages: Stage[] = [];
  if (aln?.total_reads) {
    stages.push({
      agent: 'genomic_pipeline',
      title: 'Sequenced reads',
      value: fmtCompact(aln.total_reads),
      detail: `${fmtPct(aln.mapped_rate)} mapped to hg38 · ${fmtPct(aln.duplication_rate)} duplicates`,
    });
  }
  if (st?.file) {
    stages.push({
      agent: aln ? 'HaplotypeCaller' : 'Provided VCF',
      title: 'Alleles in the VCF',
      value: fmtInt(st.file.alleles_called),
      detail: `${fmtPct(st.file.pass_rate)} PASS · ${st.file.records_read} records read`,
    });
  }
  if (panel) {
    stages.push({
      agent: 'variant_annotation',
      title: 'Panel variants',
      value: fmtInt(panel.variants_in_panel),
      detail: `${fmtPct(panel.clinvar_annotated_rate)} ClinVar-annotated ${report.genomic_findings.annotation?.version ?? ''}`,
    });
    stages.push({
      agent: 'vcf_analysis',
      title: 'Clinical QC passed',
      value: fmtInt(panel.clinical_qc_pass),
      detail: `${fmtPct(panel.clinical_qc_pass_rate)} · QUAL, DP, VAF, FILTER`,
    });
    stages.push({
      agent: 'vcf_analysis',
      title: 'Pathogenic / to confirm',
      value: fmtInt(plp),
      detail: `${cats?.vus ?? 0} VUS · ${cats?.conflicting ?? 0} conflicting`,
    });
  }
  stages.push({
    agent: 'prediction',
    title: 'Risk level',
    value: report.clinical_prediction.risk_level,
    detail: report.clinical_prediction.decision_method ?? 'germlineiq-rules-v1.1',
  });

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-card dark:border-slate-800 dark:bg-slate-900 dark:shadow-card-dark">
      <h3 className="text-sm font-bold text-slate-900 dark:text-white">From FASTQ to report</h3>
      <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
        What each orchestrator agent received and produced for this patient.
      </p>
      <ol className="mt-4 flex flex-wrap items-stretch gap-2">
        {stages.map((s, i) => (
          <li key={s.title} className="flex items-stretch gap-2">
            <div className="w-[9.5rem] rounded-xl border border-slate-200 bg-slate-50/70 p-3 dark:border-slate-800 dark:bg-slate-800/40">
              <p className="font-mono text-[10px] uppercase tracking-wide text-dna-600 dark:text-dna-400">{s.agent}</p>
              <p className="mt-1 text-[11px] font-medium text-slate-500 dark:text-slate-400">{s.title}</p>
              <p className="text-xl font-semibold tracking-tight text-slate-900 dark:text-white">{s.value}</p>
              <p className="mt-1 text-[10.5px] leading-snug text-slate-500 dark:text-slate-400">{s.detail}</p>
            </div>
            {i < stages.length - 1 && <ChevronRight className="h-4 w-4 self-center text-slate-300 dark:text-slate-600" aria-hidden />}
          </li>
        ))}
      </ol>
    </section>
  );
}
