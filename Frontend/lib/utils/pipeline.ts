import type { PipelineStep } from '@/types/api';

export const FASTQ_PIPELINE_STEPS: PipelineStep[] = [
  {
    id: 'data_manager',
    label: 'Préparation des données',
    description: 'Validation des FASTQ R1/R2 sur le serveur',
  },
  {
    id: 'parabricks',
    label: 'Appel de variants',
    description:
      'BWA-MEM → duplicats → BQSR → HaplotypeCaller sur le panel (Parabricks GPU ≥ 16 Go, sinon GATK4 CPU)',
  },
  {
    id: 'variant_annotation',
    label: 'Annotation ClinVar',
    description: 'Variants du panel annotés avec ClinVar (version tracée)',
  },
  {
    id: 'vcf_analysis',
    label: 'Analyse du panel',
    description: 'Contrôle qualité clinique et classification (13 gènes germinaux)',
  },
  {
    id: 'prediction',
    label: 'Interprétation clinique',
    description: 'Risque par règles explicites ; commentaire BioGPT non décisionnel',
  },
  {
    id: 'report',
    label: 'Rapport',
    description: 'Rapport clinique JSON archivé dans le dossier patient',
  },
];

export function normalizeStepId(step: string): string {
  if (step === 'genomic_pipeline') return 'parabricks';
  return step;
}

/** Étapes affichées : celles du plan renvoyé par l'API, sinon le pipeline FASTQ complet. */
export function plannedSteps(plan?: string[] | null): PipelineStep[] {
  if (!plan?.length) return FASTQ_PIPELINE_STEPS;
  const ids = plan.map(normalizeStepId);
  const steps = FASTQ_PIPELINE_STEPS.filter((s) => ids.includes(s.id));
  return steps.length ? steps : FASTQ_PIPELINE_STEPS;
}

export function stepState(
  stepId: string,
  stepsCompleted: string[],
  currentStep: string | null | undefined,
  jobStatus: string,
): 'pending' | 'active' | 'done' | 'failed' {
  const normalized = normalizeStepId(stepId);
  const done = stepsCompleted.map(normalizeStepId);
  const current = currentStep ? normalizeStepId(currentStep) : null;

  if (jobStatus === 'failed' && current === normalized) return 'failed';
  if (done.includes(normalized)) return 'done';
  if (current === normalized) return 'active';
  if (
    jobStatus === 'running' &&
    !current &&
    done.length === 0 &&
    normalized === 'data_manager'
  ) {
    return 'active';
  }
  if (jobStatus === 'queued' && normalized === 'data_manager' && !done.length) {
    return 'pending';
  }
  return 'pending';
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m} min ${s} s`;
}

export function riskStyles(level: string): {
  badge: string;
  border: string;
  text: string;
} {
  const l = level.toUpperCase();
  if (l === 'HIGH' || l === 'ELEVATED') {
    return {
      badge: 'bg-clinical-high/15 text-clinical-high border-clinical-high/40',
      border: 'border-clinical-high/50',
      text: 'text-clinical-high',
    };
  }
  if (l === 'MEDIUM' || l === 'MODERATE') {
    return {
      badge: 'bg-clinical-medium/15 text-clinical-medium border-clinical-medium/40',
      border: 'border-clinical-medium/50',
      text: 'text-clinical-medium',
    };
  }
  if (l === 'LOW') {
    return {
      badge: 'bg-clinical-low/15 text-clinical-low border-clinical-low/40',
      border: 'border-clinical-low/50',
      text: 'text-clinical-low',
    };
  }
  return {
    badge: 'bg-slate-500/15 text-slate-500 border-slate-500/40',
    border: 'border-slate-500/50',
    text: 'text-slate-500',
  };
}
