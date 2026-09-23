import axios from 'axios';
import type {
  AnalyzeRequest,
  AnalyzeResponse,
  AssistantChatRequest,
  AssistantChatResponse,
  HealthResponse,
  JobStatusResponse,
  UploadFastqParams,
} from '@/types/api';
import { INPUT_LOCATION_LABEL, IS_LOCAL } from '@/lib/deployment';

// Par défaut : appels relatifs (/api/v1/…, /health) relayés vers le backend par
// les rewrites Next.js (next.config.mjs). NEXT_PUBLIC_API_URL force une URL directe.
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
      return 'Endpoint introuvable — vérifiez que le backend est démarré (bash scripts/start.sh).';
    }
    if (status === 0 || err.code === 'ERR_NETWORK') {
      return `Réseau : impossible de joindre l'API (${baseURL || 'proxy Next.js'}). Vérifiez que le backend tourne et, en accès direct, CORS_ORIGINS.`;
    }
    if (typeof detail === 'string') return detail;
    if (detail && typeof detail === 'object' && 'detail' in detail) {
      return String((detail as { detail: unknown }).detail);
    }
    return err.message || 'Erreur API';
  }
  return 'Erreur inattendue';
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

export const S3_URI_PATTERN = /^s3:\/\/[a-z0-9.\-]+\/.+/i;
/** Chemin absolu sur le serveur (mode local) — le backend vérifie qu'il est sous LOCAL_DATA_ROOT. */
export const SERVER_PATH_PATTERN = /^\/[^\s]+$/;
const INPUT_PATTERN = IS_LOCAL ? SERVER_PATH_PATTERN : S3_URI_PATTERN;
const INPUT_FORMAT_HINT = IS_LOCAL
  ? 'chemin absolu sur le serveur, ex. /data/zaynb/patients/ID/input/R1.fastq.gz'
  : 's3://bucket/chemin';
export const PATIENT_ID_PATTERN = /^[A-Za-z0-9_\-]+$/;

export function validateAnalyzeForm(values: AnalyzeRequest): string | null {
  if (!values.patient_id.trim()) {
    return 'Le Patient ID est obligatoire.';
  }
  if (!PATIENT_ID_PATTERN.test(values.patient_id.trim())) {
    return 'Patient ID invalide (lettres, chiffres, _ et - uniquement).';
  }
  if (!values.s3_uri_r1.trim()) {
    return `Le ${INPUT_LOCATION_LABEL} FASTQ R1 est obligatoire.`;
  }
  if (!values.s3_uri_r2.trim()) {
    return `Le ${INPUT_LOCATION_LABEL} FASTQ R2 est obligatoire.`;
  }
  if (!INPUT_PATTERN.test(values.s3_uri_r1.trim())) {
    return `R1 invalide (format attendu : ${INPUT_FORMAT_HINT}).`;
  }
  if (!INPUT_PATTERN.test(values.s3_uri_r2.trim())) {
    return `R2 invalide (format attendu : ${INPUT_FORMAT_HINT}).`;
  }
  if (values.s3_uri_r1.trim() === values.s3_uri_r2.trim()) {
    return 'Les chemins FASTQ R1 et R2 doivent être distincts.';
  }
  return null;
}
