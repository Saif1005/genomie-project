import type { VariantCategory, VariantRow } from '@/types/api';

export const CATEGORY_ORDER: VariantCategory[] = [
  'pathogenic_confirmed',
  'pathogenic_to_confirm',
  'lof_to_confirm',
  'conflicting',
  'vus',
  'likely_benign',
  'benign',
  'other_clinvar',
  'not_in_clinvar',
];

export const CATEGORY_LABELS: Record<VariantCategory, string> = {
  pathogenic_confirmed: 'Pathogenic — confirmed',
  pathogenic_to_confirm: 'Pathogenic — to confirm',
  lof_to_confirm: 'Loss of function — to assess',
  conflicting: 'Conflicting classifications',
  vus: 'Uncertain significance (VUS)',
  likely_benign: 'Likely benign',
  benign: 'Benign',
  other_clinvar: 'Other ClinVar class',
  not_in_clinvar: 'Not in ClinVar',
};

/** Grouping into 4 clinical families (3 categorical colours + critical status for P/LP). */
export type CategoryGroup = 'pathogenic' | 'uncertain' | 'benign' | 'unclassified';

export const GROUP_OF: Record<VariantCategory, CategoryGroup> = {
  pathogenic_confirmed: 'pathogenic',
  pathogenic_to_confirm: 'pathogenic',
  lof_to_confirm: 'pathogenic',
  conflicting: 'uncertain',
  vus: 'uncertain',
  likely_benign: 'benign',
  benign: 'benign',
  other_clinvar: 'unclassified',
  not_in_clinvar: 'unclassified',
};

export const GROUPS: { id: CategoryGroup; label: string; color: string }[] = [
  { id: 'benign', label: 'Benign / likely benign', color: 'var(--viz-series-1)' },
  { id: 'uncertain', label: 'VUS / conflicting', color: 'var(--viz-series-2)' },
  { id: 'unclassified', label: 'Not in ClinVar / other', color: 'var(--viz-series-3)' },
  { id: 'pathogenic', label: 'Pathogenic / to confirm', color: 'var(--viz-critical)' },
];

export function isClinicallyRelevant(c: VariantCategory): boolean {
  return GROUP_OF[c] === 'pathogenic' || GROUP_OF[c] === 'uncertain';
}

const nf = new Intl.NumberFormat('en-US');

export function fmtInt(n: number | null | undefined): string {
  return n == null ? '—' : nf.format(n);
}

export function fmtCompact(n: number | null | undefined): string {
  if (n == null) return '—';
  if (Math.abs(n) >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
  if (Math.abs(n) >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (Math.abs(n) >= 1e4) return `${(n / 1e3).toFixed(1)}k`;
  return nf.format(n);
}

export function fmtPct(r: number | null | undefined, digits = 1): string {
  return r == null ? '—' : `${(r * 100).toFixed(digits)}%`;
}

export function fmtNum(n: number | null | undefined, digits = 2): string {
  return n == null ? '—' : n.toFixed(digits);
}

export function binLabel(lo: number, hi: number | null, digits = 0): string {
  const f = (x: number) => (digits ? x.toFixed(digits) : nf.format(x));
  return hi == null ? `≥ ${f(lo)}` : `${f(lo)}–${f(hi)}`;
}

export function variantKey(v: VariantRow): string {
  return `${v.chromosome}:${v.position}:${v.ref}:${v.alt}`;
}

export function stepLabel(step: string): string {
  const labels: Record<string, string> = {
    data_manager: 'Data preparation',
    parabricks: 'Variant calling',
    genomic_pipeline: 'Variant calling',
    variant_annotation: 'ClinVar annotation',
    vcf_analysis: 'Panel analysis',
    prediction: 'Interpretation',
    report: 'Report',
    llm_training: 'LoRA data',
  };
  return labels[step] ?? step;
}
