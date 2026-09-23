'use client';

import {
  FileUp,
  Loader2,
  Play,
  Upload,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { IS_LOCAL } from '@/lib/deployment';
import {
  isFastqFile,
  PATIENT_ID_PATTERN,
  uploadAndAnalyze,
} from '@/lib/api';

interface FastqUploadFormProps {
  onJobStarted: (jobId: string, patientId: string) => void;
  onFilesSelected?: (r1: File | null, r2: File | null) => void;
  disabled?: boolean;
}

export default function FastqUploadForm({
  onJobStarted,
  onFilesSelected,
  disabled,
}: FastqUploadFormProps) {
  const [patientId, setPatientId] = useState('');
  const [r1, setR1] = useState<File | null>(null);
  const [r2, setR2] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [uploadPct, setUploadPct] = useState(0);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    onFilesSelected?.(r1, r2);
  }, [r1, r2, onFilesSelected]);

  const assignFiles = useCallback((files: FileList | File[]) => {
    const list = Array.from(files).filter(isFastqFile);
    if (!list.length) {
      setError('Sélectionnez des fichiers FASTQ (.fastq.gz, .fq.gz, .fastq, .fq).');
      return;
    }
    setError(null);
    if (list.length >= 1 && !r1) setR1(list[0]);
    else if (list.length >= 1) setR1(list[0]);
    if (list.length >= 2) setR2(list[1]);
    else if (list.length === 1 && r1 && !r2) setR2(list[0]);
  }, [r1, r2]);

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    assignFiles(e.dataTransfer.files);
  };

  const validate = (): string | null => {
    if (!patientId.trim()) return 'Patient ID obligatoire.';
    if (!PATIENT_ID_PATTERN.test(patientId.trim())) {
      return 'Patient ID invalide.';
    }
    if (!r1) return 'FASTQ R1 obligatoire.';
    if (!r2) return 'FASTQ R2 obligatoire.';
    if (!isFastqFile(r1) || !isFastqFile(r2)) {
      return 'Extensions FASTQ invalides.';
    }
    if (r1.name === r2.name && r1.size === r2.size) {
      return 'R1 et R2 doivent être des fichiers distincts.';
    }
    return null;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const v = validate();
    if (v) {
      setError(v);
      return;
    }
    setLoading(true);
    setUploadPct(0);
    setError(null);
    try {
      const res = await uploadAndAnalyze(
        { patient_id: patientId.trim(), fastq_r1: r1!, fastq_r2: r2! },
        setUploadPct,
      );
      onJobStarted(res.job_id, res.patient_id);
    } catch {
      setError('Échec upload ou lancement. Vérifiez la taille des fichiers et l\'API.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-card dark:border-slate-800 dark:bg-slate-900 dark:shadow-card-dark sm:p-8">
      <div className="mb-6">
        <h2 className="text-lg font-bold text-slate-900 dark:text-white">
          Upload FASTQ direct
        </h2>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          Glissez vos fichiers paired-end — {IS_LOCAL ? 'enregistrement sur le serveur' : 'upload S3 automatique'} puis lancement du workflow
          multi-agents.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-5">
        <div>
          <label
            htmlFor="upload_patient_id"
            className="mb-1.5 block text-sm font-semibold text-slate-700 dark:text-slate-300"
          >
            Patient ID <span className="text-clinical-high">*</span>
          </label>
          <input
            id="upload_patient_id"
            value={patientId}
            onChange={(e) => setPatientId(e.target.value)}
            placeholder="PATIENT001"
            disabled={disabled || loading}
            className="w-full rounded-lg border border-slate-300 bg-slate-50 px-4 py-2.5 font-mono text-sm outline-none focus:border-dna-500 focus:ring-2 focus:ring-dna-500/20 dark:border-slate-600 dark:bg-slate-800 dark:text-white"
          />
        </div>

        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
          className={`rounded-xl border-2 border-dashed px-6 py-10 text-center transition ${
            dragOver
              ? 'border-dna-500 bg-dna-500/10'
              : 'border-slate-300 bg-slate-50 dark:border-slate-600 dark:bg-slate-800/40'
          }`}
        >
          <Upload className="mx-auto h-10 w-10 text-dna-500" />
          <p className="mt-3 text-sm font-medium text-slate-700 dark:text-slate-300">
            Déposez vos FASTQ ici (R1 + R2)
          </p>
          <p className="mt-1 text-xs text-slate-500">.fastq.gz · .fq.gz · .fastq · .fq</p>
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            disabled={disabled || loading}
            className="mt-4 text-sm font-semibold text-dna-600 hover:text-dna-500 dark:text-dna-400"
          >
            Parcourir les fichiers
          </button>
          <input
            ref={inputRef}
            type="file"
            accept=".fastq.gz,.fq.gz,.fastq,.fq"
            multiple
            className="hidden"
            onChange={(e) => e.target.files && assignFiles(e.target.files)}
          />
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <FileChip label="R1" file={r1} onClear={() => setR1(null)} />
          <FileChip label="R2" file={r2} onClear={() => setR2(null)} />
        </div>

        {loading && uploadPct > 0 && (
          <div>
            <div className="mb-1 flex justify-between text-xs text-slate-500">
              <span>Upload vers l&apos;API</span>
              <span className="font-mono">{uploadPct}%</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
              <div
                className="h-full bg-dna-500 transition-all"
                style={{ width: `${uploadPct}%` }}
              />
            </div>
          </div>
        )}

        {error && (
          <div className="rounded-lg border border-clinical-high/40 bg-clinical-high/10 px-4 py-3 text-sm text-clinical-high">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={disabled || loading}
          className="inline-flex items-center gap-2 rounded-lg bg-gradient-to-r from-dna-600 to-sky-600 px-5 py-2.5 text-sm font-semibold text-white shadow-md disabled:opacity-50"
        >
          {loading ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              Upload &amp; lancement…
            </>
          ) : (
            <>
              <Play className="h-4 w-4" />
              Lancer le workflow
            </>
          )}
        </button>
      </form>
    </section>
  );
}

function FileChip({
  label,
  file,
  onClear,
}: {
  label: string;
  file: File | null;
  onClear: () => void;
}) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 dark:border-slate-700 dark:bg-slate-800/60">
      <FileUp className="h-4 w-4 shrink-0 text-dna-500" />
      <div className="min-w-0 flex-1">
        <p className="text-xs font-bold text-dna-600 dark:text-dna-400">{label}</p>
        <p className="truncate font-mono text-xs text-slate-600 dark:text-slate-400">
          {file ? `${file.name} (${(file.size / 1e6).toFixed(1)} Mo)` : '—'}
        </p>
      </div>
      {file && (
        <button type="button" onClick={onClear} className="text-slate-400 hover:text-slate-600">
          <X className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}
