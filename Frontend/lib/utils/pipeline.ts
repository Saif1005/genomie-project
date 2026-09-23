import type { PipelineStep } from '@/types/api';
import { IS_LOCAL } from '@/lib/deployment';

export const FASTQ_PIPELINE_STEPS: PipelineStep[] = [
  {
    id: 'data_manager',
    label: 'Data Download',
    description: IS_LOCAL
      ? 'Validation des FASTQ sur le serveur'
      : 'Téléchargement et validation des FASTQ depuis S3',
  },
  {
    id: 'parabricks',
    label: 'Parabricks Alignment',
    description: IS_LOCAL
      ? 'fq2bam → BQSR → HaplotypeCaller (GPU ≥ 16 Go, sinon GATK4 CPU)'
      : 'fq2bam → BQSR → HaplotypeCaller (GATK GPU)',
  },
  {
    id: 'vcf_analysis',
    label: 'VCF Analysis',
    description: 'Panel gènes cancer du sein — variants pathogènes',
  },
  {
    id: 'prediction',
    label: 'BioGPT Inference',
    description: 'Inférence clinique et évaluation du risque',
  },
];

export function normalizeStepId(step: string): string {
  if (step === 'genomic_pipeline') return 'parabricks';
  return step;
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
