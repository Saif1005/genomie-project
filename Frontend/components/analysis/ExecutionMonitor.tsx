'use client';

import {
  AlertCircle,
  CheckCircle2,
  Circle,
  Loader2,
} from 'lucide-react';
import { normalizeStepId, plannedSteps, stepState } from '@/lib/utils/pipeline';
import type { JobStatusResponse } from '@/types/api';

interface ExecutionMonitorProps {
  job: JobStatusResponse | undefined;
  isLoading?: boolean;
  jobId: string;
}

function StepIcon({ state }: { state: ReturnType<typeof stepState> }) {
  if (state === 'done') {
    return <CheckCircle2 className="h-5 w-5 text-clinical-low" />;
  }
  if (state === 'active') {
    return <Loader2 className="h-5 w-5 animate-spin text-dna-500" />;
  }
  if (state === 'failed') {
    return <AlertCircle className="h-5 w-5 text-clinical-high" />;
  }
  return <Circle className="h-5 w-5 text-slate-300 dark:text-slate-600" />;
}

export default function ExecutionMonitor({
  job,
  isLoading,
  jobId,
}: ExecutionMonitorProps) {
  const status = job?.status ?? 'queued';
  const stepsCompleted = job?.steps_completed ?? [];
  const currentStep = job?.current_step;
  // Étapes réellement planifiées par l'orchestrateur (ex. VCF fourni : pas d'alignement)
  const steps = plannedSteps(job?.plan);

  const completedCount = steps.filter((s) =>
    stepsCompleted.map(normalizeStepId).includes(normalizeStepId(s.id)),
  ).length;
  const progressPct = Math.round((completedCount / steps.length) * 100);

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-card dark:border-slate-800 dark:bg-slate-900 dark:shadow-card-dark sm:p-8">
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-bold text-slate-900 dark:text-white">
            Suivi d&apos;exécution
          </h2>
          <p className="mt-1 font-mono text-xs text-slate-500 dark:text-slate-400">
            job_id: {jobId}
          </p>
          {job?.progress_message && (
            <p className="mt-2 text-sm text-dna-700 dark:text-dna-400">
              {job.progress_message}
            </p>
          )}
        </div>
        <StatusBadge status={status} />
      </div>

      <div className="mb-8">
        <div className="mb-2 flex justify-between text-xs font-medium text-slate-600 dark:text-slate-400">
          <span>Progression globale</span>
          <span className="font-mono">{progressPct}%</span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
          <div
            className="h-full rounded-full bg-gradient-to-r from-dna-500 to-sky-500 transition-all duration-500"
            style={{ width: `${status === 'completed' ? 100 : progressPct}%` }}
          />
        </div>
      </div>

      <ol className="relative space-y-0">
        {steps.map((step, index) => {
          const state = stepState(
            step.id,
            stepsCompleted,
            currentStep,
            status,
          );
          const isLast = index === steps.length - 1;

          return (
            <li key={step.id} className="relative flex gap-4 pb-8 last:pb-0">
              {!isLast && (
                <span
                  className={`absolute left-[10px] top-8 h-full w-0.5 ${
                    state === 'done'
                      ? 'bg-clinical-low/60'
                      : 'bg-slate-200 dark:bg-slate-700'
                  }`}
                />
              )}
              <div className="relative z-10 mt-0.5 shrink-0">
                <StepIcon state={state} />
              </div>
              <div
                className={`flex-1 rounded-xl border px-4 py-3 transition ${
                  state === 'active'
                    ? 'border-dna-500/50 bg-dna-500/5 dark:bg-dna-500/10'
                    : state === 'done'
                      ? 'border-clinical-low/30 bg-clinical-low/5'
                      : state === 'failed'
                        ? 'border-clinical-high/40 bg-clinical-high/5'
                        : 'border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-800/50'
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <p className="font-semibold text-slate-900 dark:text-white">
                    {step.label}
                  </p>
                  <StepLabel state={state} />
                </div>
                <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
                  {step.description}
                </p>
              </div>
            </li>
          );
        })}
      </ol>

      {isLoading && !job && (
        <p className="mt-4 flex items-center gap-2 text-sm text-slate-500">
          <Loader2 className="h-4 w-4 animate-spin" />
          Connexion au job…
        </p>
      )}

      {job?.error && (
        <div className="mt-6 rounded-lg border border-clinical-high/40 bg-clinical-high/10 px-4 py-3 text-sm text-clinical-high">
          <strong>Erreur pipeline :</strong> {job.error}
        </div>
      )}
    </section>
  );
}

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    queued:
      'border-slate-300 bg-slate-100 text-slate-700 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-300',
    running:
      'border-dna-500/40 bg-dna-500/10 text-dna-700 dark:text-dna-400',
    completed:
      'border-clinical-low/40 bg-clinical-low/10 text-clinical-low',
    failed:
      'border-clinical-high/40 bg-clinical-high/10 text-clinical-high',
  };
  const labels: Record<string, string> = {
    queued: 'En file',
    running: 'En cours',
    completed: 'Terminé',
    failed: 'Échec',
  };
  return (
    <span
      className={`rounded-full border px-3 py-1 text-xs font-bold uppercase tracking-wide ${styles[status] ?? styles.queued}`}
    >
      {labels[status] ?? status}
    </span>
  );
}

function StepLabel({ state }: { state: ReturnType<typeof stepState> }) {
  const map = {
    pending: 'En attente',
    active: 'En cours',
    done: 'Terminé',
    failed: 'Échec',
  };
  const colors = {
    pending: 'text-slate-400',
    active: 'text-dna-600 dark:text-dna-400',
    done: 'text-clinical-low',
    failed: 'text-clinical-high',
  };
  return (
    <span className={`text-xs font-semibold uppercase ${colors[state]}`}>
      {map[state]}
    </span>
  );
}
