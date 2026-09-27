import axios from 'axios';
import type {
  AnalyzeRequest,
  AnalyzeResponse,
  AssistantChatRequest,
  AssistantChatResponse,
  HealthResponse,
  JobStatusResponse,
  JobSummary,
  UploadFastqParams,
} from '@/types/api';

// Default: relative calls (/api/v1/…, /health) proxied to the backend by the
// Next.js rewrites (next.config.mjs). NEXT_PUBLIC_API_URL forces a direct URL.
const baseURL = process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, '') || '';

export const apiClient = axios.create({
  baseURL,
  timeout: 30_000,
  headers: { 'Content-Type': 'application/json' },
});

export const uploadClient = axios.create({
  baseURL,
  timeout: 3_600_000,
});

export async function checkHealth(): Promise<HealthResponse> {
  const { data } = await apiClient.get<HealthResponse>('/health');
  return data;
}

export interface FineTuneIteration {
  iteration: string;
  description: string;
  val_perplexity_best: number;
  train_seconds: number;
  accepted: boolean;
  checks: Record<string, boolean>;
  base: Record<string, number>;
  fine_tuned: Record<string, number>;
}

export interface BiogptModelInfo {
  base_model: string;
  adapter_in_service: string | null;
  commentary_enabled: boolean;
  fine_tuning: {
    iterations: FineTuneIteration[];
    decision: string;
    production_default: string;
    statistical_note: string;
  } | null;
  last_training: {
    trainable_parameters: number;
    total_parameters: number;
    device: string;
    duration_s: number;
    versions: Record<string, string>;
  } | null;
  corpus: {
    version: string;
    retrieved_at: string;
    abstracts_kept: number;
    pmids_found: number;
    per_gene: Record<string, number>;
    date_range: string[];
    sha256: string;
  } | null;
  knowledge: { version: string; curated: Record<string, string[]> };
  statistics_model?: StatisticsModelInfo;
}

export interface StatsEvalMetrics {
  examples_scored: number;
  sentence_precision: number;
  topic_coverage: number;
  number_accuracy: number | null;
  status_accuracy: number | null;
  risk_errors: number;
  final_verified_rate: number;
  generated_sentences: number;
  verified_sentences: number;
}

export interface StatisticsModelInfo {
  enabled: boolean;
  in_service: boolean;
  adapter_path: string;
  promoted: { version: string; promoted_at: string } | null;
  versions: Record<string, string>;
  dataset: { created_at: string; seed: number; splits: Record<string, { examples: number; sha256: string; fastq_mode: number; risk_levels: Record<string, number> }> } | null;
  training: { best_val_loss: number; steps: number; duration_s: number; trainable_parameters: number; total_parameters: number; device: string } | null;
  evaluation: {
    gate_thresholds: Record<string, number>;
    promotion: { passed: boolean; per_split: Record<string, { passed: boolean; checks: Record<string, boolean> }> };
    base_zero_shot: Record<string, StatsEvalMetrics>;
    fine_tuned: Record<string, StatsEvalMetrics>;
  } | null;
}

export async function listJobs(limit = 200): Promise<JobSummary[]> {
  const { data } = await apiClient.get<JobSummary[]>('/api/v1/jobs', { params: { limit } });
  return data;
}

/** Latest multi-agent benchmark (python -m benchmarks.multiagent); loosely typed JSON. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function getLatestBenchmark(): Promise<any> {
  const { data } = await apiClient.get('/api/v1/benchmarks/latest');
  return data;
}

export async function getBiogptModel(): Promise<BiogptModelInfo> {
  const { data } = await apiClient.get<BiogptModelInfo>('/api/v1/models/biogpt');
  return data;
}

export async function startAnalysis(
  payload: AnalyzeRequest,
): Promise<AnalyzeResponse> {
  const { data } = await apiClient.post<AnalyzeResponse>(
    '/api/v1/analyze',
    payload,
  );
  return data;
}

export async function getJobStatus(jobId: string): Promise<JobStatusResponse> {
  const { data } = await apiClient.get<JobStatusResponse>(
    `/api/v1/jobs/${jobId}`,
  );
  return data;
}

export async function getClinicalReport(jobId: string) {
  const { data } = await apiClient.get(`/api/v1/jobs/${jobId}/report`);
  return data;
}

export async function sendAssistantMessage(
  payload: AssistantChatRequest,
): Promise<AssistantChatResponse> {
  const { data } = await apiClient.post<AssistantChatResponse>(
    '/api/v1/assistant/chat',
    payload,
  );
  return data;
}

export function formatApiError(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const status = err.response?.status;
    const detail = err.response?.data;
    if (status === 404) {
      return 'Endpoint not found — check that the backend is running (bash scripts/start.sh).';
    }
    if (status === 0 || err.code === 'ERR_NETWORK') {
      return `Network: cannot reach the API (${baseURL || 'Next.js proxy'}). Check that the backend is running and, for direct access, CORS_ORIGINS.`;
    }
    if (typeof detail === 'string') return detail;
    if (detail && typeof detail === 'object' && 'detail' in detail) {
      return String((detail as { detail: unknown }).detail);
    }
    return err.message || 'API error';
  }
  return 'Unexpected error';
}

export async function uploadAndAnalyze(
  params: UploadFastqParams,
  onUploadProgress?: (pct: number) => void,
): Promise<AnalyzeResponse> {
  const form = new FormData();
  form.append('patient_id', params.patient_id.trim());
  form.append('fastq_r1', params.fastq_r1);
  form.append('fastq_r2', params.fastq_r2);

  const { data } = await uploadClient.post<AnalyzeResponse>(
    '/api/v1/analyze/upload',
    form,
    {
      onUploadProgress: (e) => {
        if (onUploadProgress && e.total) {
          onUploadProgress(Math.round((e.loaded / e.total) * 100));
        }
      },
    },
  );
  return data;
}

export const FASTQ_EXTENSIONS = ['.fastq.gz', '.fq.gz', '.fastq', '.fq'];

export function isFastqFile(file: File): boolean {
  const name = file.name.toLowerCase();
  return FASTQ_EXTENSIONS.some((ext) => name.endsWith(ext));
}

/** Absolute path on the server — the backend checks that it is under LOCAL_DATA_ROOT. */
export const SERVER_PATH_PATTERN = /^\/[^\s]+$/;
const INPUT_FORMAT_HINT =
  'absolute path on the server, e.g. /data/germlineiq/patients/ID/input/R1.fastq.gz';
export const PATIENT_ID_PATTERN = /^[A-Za-z0-9_\-]+$/;

export function validateAnalyzeForm(values: AnalyzeRequest): string | null {
  if (!values.patient_id.trim()) {
    return 'Patient ID is required.';
  }
  if (!PATIENT_ID_PATTERN.test(values.patient_id.trim())) {
    return 'Invalid patient ID (letters, digits, _ and - only).';
  }
  if (!values.fastq_r1.trim()) {
    return 'The server path of FASTQ R1 is required.';
  }
  if (!values.fastq_r2.trim()) {
    return 'The server path of FASTQ R2 is required.';
  }
  if (!SERVER_PATH_PATTERN.test(values.fastq_r1.trim())) {
    return `R1 invalide (format attendu : ${INPUT_FORMAT_HINT}).`;
  }
  if (!SERVER_PATH_PATTERN.test(values.fastq_r2.trim())) {
    return `R2 invalide (format attendu : ${INPUT_FORMAT_HINT}).`;
  }
  if (values.fastq_r1.trim() === values.fastq_r2.trim()) {
    return 'FASTQ R1 and R2 paths must be different.';
  }
  return null;
}
