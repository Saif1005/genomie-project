export type JobStatus = 'queued' | 'running' | 'completed' | 'failed';

export interface AnalyzeRequest {
  patient_id: string;
  fastq_r1: string;
  fastq_r2: string;
  train_llm?: boolean;
}

export interface AnalyzeResponse {
  job_id: string;
  status: JobStatus;
  patient_id: string;
  plan?: string[];
  message: string;
}

export interface GATKMetrics {
  QUAL?: number | null;
  DP?: number | null;
  VAF?: number | null;
}

export interface PathogenicVariant {
  gene: string;
  chromosome: string;
  position: number;
  mutation: string;
  gatk_metrics: GATKMetrics;
  /** Classification ClinVar (Pathogenic, Likely_pathogenic, …) */
  pathogenicity?: string;
  inheritance?: string | null;
  zygosity?: string | null;
  penetrance?: string | null;
  hgvs?: string | null;
  rsid?: string | null;
  review_stars?: number | null;
  review_status?: string | null;
  conditions?: string | null;
  qc_status?: string | null;
  qc_flags?: string[];
  note?: string | null;
}

export interface GenomicFindings {
  breast_cancer_panel_analyzed?: string[];
  pathogenic_variants_detected: PathogenicVariant[];
  variants_to_confirm?: PathogenicVariant[];
  vus_detected?: PathogenicVariant[];
  conflicting_variants?: PathogenicVariant[];
  breast_cancer_risk_detected?: boolean;
  identified_pathogenic_genes?: string[];
  variants_in_panel?: number;
  annotation?: { source?: string; version?: string };
}

export interface SystemMetrics {
  execution_time_seconds: number;
  pipeline_engine: string;
  hardware: string;
  steps_completed: string[];
  orchestration?: { engine?: string; router?: string; plan?: string[] };
}

export type RiskLevel = 'HIGH' | 'MODERATE' | 'LOW' | 'INDETERMINATE' | string;

export interface ClinicalPrediction {
  model: string;
  risk_level: RiskLevel;
  diagnostic_conclusion: string;
  clinical_summary: string;
  rationale?: string[];
  limitations?: string[];
  decision_method?: string | null;
  model_commentary?: string | null;
  commentary_model?: string | null;
  commentary_verification?: CommentaryVerification | null;
  statistics_interpretation?: StatisticsInterpretation | null;
  quality_warnings?: string[];
  legal_disclaimer: string;
  status: string;
}

export interface Reproducibility {
  input_sha256?: string;
  panel_version?: string;
  annotation_source?: string;
  annotation_version?: string;
  qc_thresholds?: Record<string, number>;
  decision_method?: string;
  software_version?: string;
}

export interface ClinicalReport {
  report_id: string;
  patient_id: string;
  generated_at: string;
  system_metrics: SystemMetrics;
  genomic_findings: GenomicFindings;
  clinical_prediction: ClinicalPrediction;
  reproducibility?: Reproducibility;
  statistics?: VcfStatistics | null;
  report_path?: string | null;
}

/* ---- VCF statistics (germlineiq-stats-v1) and alignment QC ---- */

export interface Describe {
  n: number;
  min: number | null;
  q1: number | null;
  median: number | null;
  q3: number | null;
  max: number | null;
  mean: number | null;
  sd: number | null;
}

export interface HistogramBin {
  lo: number;
  hi: number | null;
  count: number;
}

export interface Distribution {
  summary: Describe;
  histogram: HistogramBin[];
}

export interface CountsSummary {
  records_read: number;
  alleles_called: number;
  pass: number;
  pass_rate: number | null;
  by_type: Record<string, number>;
  by_zygosity: Record<string, number>;
  by_filter: Record<string, number>;
  by_chromosome: Record<string, number>;
  transitions_pass_snv: number;
  transversions_pass_snv: number;
  ti_tv: number | null;
  het_hom_ratio: number | null;
  multiallelic_split_alleles: number;
}

export type VariantCategory =
  | 'pathogenic_confirmed'
  | 'pathogenic_to_confirm'
  | 'lof_to_confirm'
  | 'conflicting'
  | 'vus'
  | 'likely_benign'
  | 'benign'
  | 'other_clinvar'
  | 'not_in_clinvar';

export interface PanelCounts extends CountsSummary {
  variants_in_panel: number;
  clinvar_annotated: number;
  clinvar_annotated_rate: number | null;
  clinical_qc_pass: number;
  clinical_qc_pass_rate: number | null;
  by_category: Record<VariantCategory, number>;
  qc_flags: Record<string, number>;
}

export interface QualityCheck {
  id: string;
  label: string;
  value: number | null;
  expected: string;
  status: 'OK' | 'WARN' | 'NA';
  explanation: string;
}

export interface GeneStats {
  gene: string;
  variants: number;
  pass: number;
  qc_pass: number;
  snv: number;
  indel: number;
  categories: Record<VariantCategory, number>;
  median_dp: number | null;
}

export interface VariantRow {
  gene: string;
  chromosome: string;
  position: number;
  ref: string;
  alt: string;
  variant_type: string;
  indel_length: number | null;
  genotype: string | null;
  zygosity: string | null;
  quality: number | null;
  dp: number | null;
  gq: number | null;
  vaf: number | null;
  filter: string;
  qc_status: string;
  qc_flags: string[];
  category: VariantCategory;
  clinvar_significance: string | null;
  review_stars: number | null;
  variation_id: string | null;
  rsid: string | null;
  hgvs: string | null;
  conditions: string | null;
}

export interface GeneCoverage {
  gene: string;
  sites: number;
  covered: number;
  fraction_covered: number | null;
  median_depth: number | null;
  zero_depth_sites: number;
}

export interface AlignmentQC {
  version: string;
  total_reads?: number | null;
  mapped_reads?: number | null;
  mapped_rate?: number | null;
  properly_paired_rate?: number | null;
  duplication_rate?: number | null;
  read_pairs_examined?: number | null;
  estimated_library_size?: number | null;
  clinvar_version?: string;
  clinvar_sites_coverage?: {
    min_depth: number;
    sites: number;
    covered: number;
    fraction_covered: number | null;
    median_depth: number | null;
    per_gene: GeneCoverage[];
    definition: string;
  };
}

export interface VcfStatistics {
  version: string;
  file: CountsSummary | null;
  panel: PanelCounts;
  distributions: {
    quality: Distribution;
    depth: Distribution;
    genotype_quality: Distribution;
    vaf_heterozygous: Distribution;
    vaf_all: Distribution;
    indel_length: Distribution;
    pass_only_quality: Describe;
  };
  per_gene: GeneStats[];
  quality_checks: QualityCheck[];
  alignment: AlignmentQC | null;
  variants: VariantRow[];
}

export interface GeneVerification {
  gene: string;
  text: string;
  verified: boolean;
  diseases: string[];
  unsupported_diseases: string[];
  other_panel_genes: string[];
  reason: string | null;
  used: 'biogpt' | 'reference';
  final_text: string;
}

export interface CommentaryVerification {
  method: string;
  knowledge_version: string;
  clinvar_version?: string | null;
  model: string;
  adapter?: string | null;
  generated: number;
  verified: number;
  per_gene: GeneVerification[];
}

/* ---- Interpretation of the VCF statistics (BioGPT fine-tuned + deterministic verifier) ---- */

export interface InterpretationClaim {
  type: 'number' | 'status' | 'risk' | 'genes' | 'none_confirmed';
  metric?: string;
  value?: string;
  claimed?: string;
  actual?: string;
  ok: boolean;
  detail?: string;
}

export interface InterpretationSentence {
  sentence: string;
  verified: boolean;
  reasons: string[];
  topics: string[];
  claims: InterpretationClaim[];
}

export interface StatisticsInterpretation {
  text: string;
  source: 'biogpt-stats' | 'reference';
  note?: string | null;
  model?: string | null;
  adapter?: string | null;
  model_output?: string;
  final_verified: boolean;
  per_topic: { topic: string; source: 'model' | 'reference'; sentence: string }[];
  metrics: {
    generated_sentences: number;
    verified_sentences: number;
    rejected_sentences: number;
    required_topics: number;
    topics_from_model: number;
    topics_from_reference: number;
  };
  sentences: InterpretationSentence[];
  versions: Record<string, string>;
}

export interface JobStatusResponse {
  job_id: string;
  status: JobStatus;
  patient_id: string;
  created_at: string;
  updated_at: string;
  mode?: string | null;
  vcf_path?: string | null;
  plan?: string[];
  current_step?: string | null;
  progress_message?: string | null;
  steps_completed: string[];
  error?: string | null;
  result?: ClinicalReport | null;
  step_timings?: StepTiming[];
  duration_s?: number | null;
  router?: string | null;
}

export interface StepTiming {
  tool: string;
  ui_step: string;
  status: 'completed' | 'cached' | 'failed' | string;
  duration: number;
  error?: string | null;
}

export interface JobSummary {
  job_id: string;
  patient_id: string;
  status: JobStatus;
  mode?: string | null;
  created_at: string;
  updated_at: string;
  duration_s?: number | null;
  risk_level?: string | null;
  identified_genes: string[];
  variants_in_panel?: number | null;
  report_id?: string | null;
}

export interface HealthResponse {
  status: string;
  service: string;
  version?: string;
  orchestrator?: string;
  pipeline_backend?: string;
  pipeline_backend_reason?: string;
  clinvar?: { path: string; available: boolean };
  data_root?: string;
  gpus?: { name: string; vram_mb: number; compute_cap?: string }[];
  panel?: { version: string; germline_genes: string[] };
  biogpt_commentary?: boolean;
}

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
}

export interface AssistantChatRequest {
  message: string;
  history?: ChatMessage[];
  context?: Record<string, unknown>;
}

export interface AssistantChatResponse {
  reply: string;
  intent: string;
  action_taken?: string | null;
  job_id?: string | null;
  patient_id?: string | null;
  missing_fields: string[];
  parsed: Record<string, unknown>;
}

export interface UploadFastqParams {
  patient_id: string;
  fastq_r1: File;
  fastq_r2: File;
}

export type PipelineStepId =
  | 'data_manager'
  | 'parabricks'
  | 'genomic_pipeline'
  | 'variant_annotation'
  | 'vcf_analysis'
  | 'prediction'
  | 'report';

export interface PipelineStep {
  id: PipelineStepId;
  label: string;
  description: string;
}
