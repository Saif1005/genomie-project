'use client';

import { Loader2, Play, RotateCcw } from 'lucide-react';
import { useState } from 'react';
import { startAnalysis, validateAnalyzeForm } from '@/lib/api';
import { INPUT_LOCATION_LABEL, IS_LOCAL, LOCAL_DATA_ROOT } from '@/lib/deployment';
import type { AnalyzeRequest } from '@/types/api';

const fastqPlaceholder = (read: 'R1' | 'R2') =>
  IS_LOCAL
    ? `${LOCAL_DATA_ROOT}/patients/PATIENT001/input/sample_${read}.fastq.gz`
    : `s3://bucket/patient/sample_${read}.fastq.gz`;

interface AnalysisFormProps {
  onJobStarted: (jobId: string, patientId: string) => void;
  disabled?: boolean;
}

const EMPTY: AnalyzeRequest = {
  patient_id: '',
  s3_uri_r1: '',
  s3_uri_r2: '',
};

export default function AnalysisForm({ onJobStarted, disabled }: AnalysisFormProps) {
  const [form, setForm] = useState<AnalyzeRequest>(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const update = (field: keyof AnalyzeRequest, value: string) => {
    setForm((prev) => ({ ...prev, [field]: value }));
    setError(null);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const validation = validateAnalyzeForm(form);
    if (validation) {
      setError(validation);
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const payload: AnalyzeRequest = {
        patient_id: form.patient_id.trim(),
        s3_uri_r1: form.s3_uri_r1.trim(),
        s3_uri_r2: form.s3_uri_r2.trim(),
      };
      const res = await startAnalysis(payload);
      onJobStarted(res.job_id, res.patient_id);
    } catch (err: unknown) {
      const msg =
        err && typeof err === 'object' && 'response' in err
          ? (err as { response?: { data?: { detail?: string } } }).response?.data
              ?.detail
          : null;
      setError(
        typeof msg === 'string'
          ? msg
          : 'Impossible de démarrer l\'analyse. Vérifiez la connexion API.',
      );
    } finally {
      setLoading(false);
    }
  };

  const handleReset = () => {
    setForm(EMPTY);
    setError(null);
  };

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-card dark:border-slate-800 dark:bg-slate-900 dark:shadow-card-dark sm:p-8">
      <div className="mb-6">
        <h2 className="text-lg font-bold text-slate-900 dark:text-white">
          Nouvelle analyse génomique
        </h2>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          Lancez le pipeline FASTQ (Parabricks GATK → VCF → BioGPT) via l&apos;orchestrateur
          multi-agents.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-5" noValidate>
        <div>
          <label
            htmlFor="patient_id"
            className="mb-1.5 block text-sm font-semibold text-slate-700 dark:text-slate-300"
          >
            Patient ID <span className="text-clinical-high">*</span>
          </label>
          <input
            id="patient_id"
            type="text"
            value={form.patient_id}
            onChange={(e) => update('patient_id', e.target.value)}
            placeholder="PATIENT001"
            disabled={disabled || loading}
            className="w-full rounded-lg border border-slate-300 bg-slate-50 px-4 py-2.5 font-mono text-sm text-slate-900 outline-none transition focus:border-dna-500 focus:ring-2 focus:ring-dna-500/20 disabled:opacity-50 dark:border-slate-600 dark:bg-slate-800 dark:text-white"
            autoComplete="off"
          />
        </div>

        <div className="grid gap-5 sm:grid-cols-2">
          <div>
            <label
              htmlFor="s3_uri_r1"
              className="mb-1.5 block text-sm font-semibold text-slate-700 dark:text-slate-300"
            >
              FASTQ R1 ({INPUT_LOCATION_LABEL}) <span className="text-clinical-high">*</span>
            </label>
            <input
              id="s3_uri_r1"
              type="text"
              value={form.s3_uri_r1}
              onChange={(e) => update('s3_uri_r1', e.target.value)}
              placeholder={fastqPlaceholder('R1')}
              disabled={disabled || loading}
              className="w-full rounded-lg border border-slate-300 bg-slate-50 px-4 py-2.5 font-mono text-xs text-slate-900 outline-none transition focus:border-dna-500 focus:ring-2 focus:ring-dna-500/20 disabled:opacity-50 dark:border-slate-600 dark:bg-slate-800 dark:text-white sm:text-sm"
            />
          </div>
          <div>
            <label
              htmlFor="s3_uri_r2"
              className="mb-1.5 block text-sm font-semibold text-slate-700 dark:text-slate-300"
            >
              FASTQ R2 ({INPUT_LOCATION_LABEL}) <span className="text-clinical-high">*</span>
            </label>
            <input
              id="s3_uri_r2"
              type="text"
              value={form.s3_uri_r2}
              onChange={(e) => update('s3_uri_r2', e.target.value)}
              placeholder={fastqPlaceholder('R2')}
              disabled={disabled || loading}
              className="w-full rounded-lg border border-slate-300 bg-slate-50 px-4 py-2.5 font-mono text-xs text-slate-900 outline-none transition focus:border-dna-500 focus:ring-2 focus:ring-dna-500/20 disabled:opacity-50 dark:border-slate-600 dark:bg-slate-800 dark:text-white sm:text-sm"
            />
          </div>
        </div>

        {error && (
          <div
            role="alert"
            className="rounded-lg border border-clinical-high/40 bg-clinical-high/10 px-4 py-3 text-sm text-clinical-high"
          >
            {error}
          </div>
        )}

        <div className="flex flex-wrap gap-3 pt-2">
          <button
            type="submit"
            disabled={disabled || loading}
            className="inline-flex items-center gap-2 rounded-lg bg-gradient-to-r from-dna-600 to-sky-600 px-5 py-2.5 text-sm font-semibold text-white shadow-md shadow-dna-500/30 transition hover:from-dna-500 hover:to-sky-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Lancement…
              </>
            ) : (
              <>
                <Play className="h-4 w-4" />
                Lancer l&apos;analyse
              </>
            )}
          </button>
          <button
            type="button"
            onClick={handleReset}
            disabled={loading}
            className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 transition hover:border-slate-400 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-300"
          >
            <RotateCcw className="h-4 w-4" />
            Réinitialiser
          </button>
        </div>
      </form>
    </section>
  );
}
