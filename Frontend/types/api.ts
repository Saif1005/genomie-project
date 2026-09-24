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
  report_path?: string | null;
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
}

export interface HealthResponse {
  status: string;
  service: string;
  version?: string;
  orchestrator?: string;
  pipeline_backend?: string;
  pipeline_backend_reason?: string;
  clinvar?: { path: string; available: boolean };
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
