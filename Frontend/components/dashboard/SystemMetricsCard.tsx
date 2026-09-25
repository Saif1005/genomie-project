import { Clock, Cpu, Layers } from 'lucide-react';
import { formatDuration } from '@/lib/utils/pipeline';
import type { SystemMetrics } from '@/types/api';

interface SystemMetricsCardProps {
  metrics: SystemMetrics;
  patientId: string;
  reportId: string;
  generatedAt: string;
}

export default function SystemMetricsCard({
  metrics,
  patientId,
  reportId,
  generatedAt,
}: SystemMetricsCardProps) {
  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-card dark:border-slate-800 dark:bg-slate-900 dark:shadow-card-dark">
      <h2 className="mb-4 text-lg font-bold text-slate-900 dark:text-white">
        Métriques système
      </h2>

      <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <MetricItem
          icon={<Clock className="h-4 w-4 text-dna-500" />}
          label="Temps d'exécution"
          value={formatDuration(metrics.execution_time_seconds)}
          mono
        />
        <MetricItem
          icon={<Cpu className="h-4 w-4 text-dna-500" />}
          label="Matériel"
          value={metrics.hardware}
        />
        <MetricItem
          icon={<Layers className="h-4 w-4 text-dna-500" />}
          label="Moteur pipeline"
          value={metrics.pipeline_engine}
        />
        <MetricItem label="Patient ID" value={patientId} mono />
        <MetricItem label="Report ID" value={reportId} mono />
        <MetricItem label="Généré le" value={generatedAt} mono />
      </dl>

      {metrics.steps_completed?.length > 0 && (
        <div className="mt-5 border-t border-slate-200 pt-4 dark:border-slate-700">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
            Étapes complétées
          </p>
          <div className="flex flex-wrap gap-2">
            {metrics.steps_completed.map((step) => (
              <span
                key={step}
                className="rounded-md border border-dna-500/30 bg-dna-500/10 px-2 py-1 font-mono text-xs text-dna-700 dark:text-dna-400"
              >
                {step}
              </span>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function MetricItem({
  icon,
  label,
  value,
  mono,
}: {
  icon?: React.ReactNode;
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="rounded-xl border border-slate-100 bg-slate-50 p-4 dark:border-slate-700 dark:bg-slate-800/50">
      <dt className="mb-1 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
        {icon}
        {label}
      </dt>
      <dd
        className={`text-sm font-semibold text-slate-900 dark:text-white ${mono ? 'font-mono text-xs break-all' : ''}`}
      >
        {value}
      </dd>
    </div>
  );
}
