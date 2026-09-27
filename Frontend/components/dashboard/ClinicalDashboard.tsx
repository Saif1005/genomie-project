'use client';

import { useState } from 'react';
import { Activity, BarChart3, Brain, ListTree, ShieldCheck } from 'lucide-react';
import AgentTimeline from '@/components/dashboard/AgentTimeline';
import AiTracePanel from '@/components/dashboard/AiTracePanel';
import ClinicalInferenceCard from '@/components/dashboard/ClinicalInferenceCard';
import DataJourney from '@/components/dashboard/DataJourney';
import PathogenicVariantsTable from '@/components/dashboard/PathogenicVariantsTable';
import QualityPanel from '@/components/dashboard/QualityPanel';
import StatsInterpretationCard from '@/components/dashboard/StatsInterpretationCard';
import VariantExplorer from '@/components/dashboard/VariantExplorer';
import VcfStatisticsPanel from '@/components/dashboard/VcfStatisticsPanel';
import type { ClinicalReport, JobStatusResponse } from '@/types/api';

type Tab = 'summary' | 'stats' | 'variants' | 'quality' | 'ai';

interface ClinicalDashboardProps {
  report: ClinicalReport;
  /** Job record (per-agent timings); optional */
  job?: JobStatusResponse;
}

export default function ClinicalDashboard({ report, job }: ClinicalDashboardProps) {
  const [tab, setTab] = useState<Tab>('summary');
  const stats = report.statistics;
  const warnings = report.clinical_prediction.quality_warnings ?? [];
  const warnCount = stats?.quality_checks.filter((c) => c.status === 'WARN').length ?? 0;

  const tabs: { id: Tab; label: string; icon: React.ReactNode; badge?: string; disabled?: boolean }[] = [
    { id: 'summary', label: 'Clinical summary', icon: <Activity className="h-4 w-4" /> },
    { id: 'stats', label: 'VCF statistics', icon: <BarChart3 className="h-4 w-4" />, disabled: !stats },
    { id: 'variants', label: 'Variants', icon: <ListTree className="h-4 w-4" />, badge: stats ? String(stats.variants.length) : undefined, disabled: !stats },
    { id: 'quality', label: 'Quality & coverage', icon: <ShieldCheck className="h-4 w-4" />, badge: warnCount ? `${warnCount} warning(s)` : undefined, disabled: !stats },
    { id: 'ai', label: 'AI & traceability', icon: <Brain className="h-4 w-4" /> },
  ];

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <div className="h-px flex-1 bg-gradient-to-r from-transparent via-dna-500/50 to-transparent" />
        <h2 className="text-sm font-bold uppercase tracking-widest text-dna-600 dark:text-dna-400">
          Report {report.report_id}
        </h2>
        <div className="h-px flex-1 bg-gradient-to-r from-transparent via-dna-500/50 to-transparent" />
      </div>

      <nav
        className="sticky top-[64px] z-30 -mx-1 flex gap-1 overflow-x-auto rounded-xl border border-slate-200 bg-white/90 p-1 backdrop-blur dark:border-slate-800 dark:bg-slate-900/90"
        role="tablist"
        aria-label="Report sections"
      >
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            disabled={t.disabled}
            onClick={() => setTab(t.id)}
            className={`flex shrink-0 items-center gap-2 rounded-lg px-3.5 py-2 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-40 ${
              tab === t.id
                ? 'bg-dna-500/10 text-dna-700 dark:text-dna-300'
                : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-white'
            }`}
          >
            {t.icon}
            {t.label}
            {t.badge && (
              <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-bold text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                {t.badge}
              </span>
            )}
          </button>
        ))}
      </nav>

      {tab === 'summary' && (
        <div className="space-y-6">
          <ClinicalInferenceCard prediction={report.clinical_prediction} />
          {report.clinical_prediction.statistics_interpretation && (
            <StatsInterpretationCard interpretation={report.clinical_prediction.statistics_interpretation} />
          )}
          {stats && <DataJourney report={report} />}
          {job?.step_timings && job.step_timings.length > 0 && (
            <AgentTimeline steps={job.step_timings} total={job.duration_s} router={job.router} />
          )}
          <PathogenicVariantsTable
            variants={report.genomic_findings.pathogenic_variants_detected}
            genes={report.genomic_findings.identified_pathogenic_genes}
          />
          {(report.genomic_findings.variants_to_confirm?.length ?? 0) > 0 && (
            <PathogenicVariantsTable
              title="Variants to confirm (orthogonal method)"
              variants={report.genomic_findings.variants_to_confirm ?? []}
              showQcReason
            />
          )}
        </div>
      )}
      {tab === 'stats' && stats && report.clinical_prediction.statistics_interpretation && (
        <StatsInterpretationCard interpretation={report.clinical_prediction.statistics_interpretation} />
      )}
      {tab === 'stats' && stats && <VcfStatisticsPanel stats={stats} thresholds={report.reproducibility?.qc_thresholds} />}
      {tab === 'variants' && stats && <VariantExplorer variants={stats.variants} />}
      {tab === 'quality' && stats && <QualityPanel stats={stats} warnings={warnings} />}
      {tab === 'ai' && <AiTracePanel report={report} />}
    </div>
  );
}
