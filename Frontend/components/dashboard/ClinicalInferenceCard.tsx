import { Brain, ShieldAlert } from 'lucide-react';
import { riskStyles } from '@/lib/utils/pipeline';
import type { ClinicalPrediction } from '@/types/api';

interface ClinicalInferenceCardProps {
  prediction: ClinicalPrediction;
}

export default function ClinicalInferenceCard({
  prediction,
}: ClinicalInferenceCardProps) {
  const styles = riskStyles(prediction.risk_level);

  return (
    <section
      className={`rounded-2xl border-2 bg-white p-6 shadow-card dark:bg-slate-900 dark:shadow-card-dark sm:p-8 ${styles.border}`}
    >
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-dna-500/20 to-sky-500/20">
            <Brain className="h-5 w-5 text-dna-600 dark:text-dna-400" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-slate-900 dark:text-white">
              Inférence clinique
            </h2>
            <p className="text-sm text-slate-600 dark:text-slate-400">
              Modèle :{' '}
              <span className="font-mono font-semibold">{prediction.model}</span>
            </p>
          </div>
        </div>
        <div
          className={`rounded-xl border px-4 py-2 text-center ${styles.badge}`}
        >
          <p className="text-[10px] font-bold uppercase tracking-widest">
            Niveau de risque
          </p>
          <p className={`text-2xl font-black ${styles.text}`}>
            {prediction.risk_level}
          </p>
        </div>
      </div>

      <div className="mb-5 rounded-xl border border-slate-200 bg-slate-50 p-4 dark:border-slate-700 dark:bg-slate-800/50">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          Conclusion diagnostique
        </p>
        <p className="mt-2 text-base font-semibold leading-relaxed text-slate-900 dark:text-white">
          {prediction.diagnostic_conclusion}
        </p>
      </div>

      {prediction.clinical_summary && (
        <div className="mb-5">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
            Synthèse clinique
          </p>
          <p className="text-sm leading-relaxed text-slate-700 dark:text-slate-300 whitespace-pre-wrap">
            {prediction.clinical_summary}
          </p>
        </div>
      )}

      {(prediction.rationale?.length ?? 0) > 0 && (
        <div className="mb-5">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
            Justification ({prediction.decision_method ?? 'règles'})
          </p>
          <ul className="list-disc space-y-1 pl-5 text-sm text-slate-700 dark:text-slate-300">
            {prediction.rationale!.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        </div>
      )}

      {prediction.model_commentary && (
        <div className="mb-5 rounded-xl border border-dashed border-slate-300 p-4 dark:border-slate-700">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
            Commentaire bibliographique {prediction.commentary_model ?? 'BioGPT'} — non décisionnel
          </p>
          <p className="text-sm italic leading-relaxed text-slate-600 dark:text-slate-400">
            {prediction.model_commentary}
          </p>
        </div>
      )}

      {(prediction.limitations?.length ?? 0) > 0 && (
        <details className="mb-5 text-sm text-slate-600 dark:text-slate-400">
          <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-slate-500">
            Limites de l&apos;analyse
          </summary>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            {prediction.limitations!.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        </details>
      )}

      <div className="flex gap-3 rounded-xl border border-amber-500/30 bg-amber-500/5 p-4 dark:bg-amber-500/10">
        <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-600 dark:text-amber-400" />
        <div>
          <p className="text-xs font-bold uppercase tracking-wide text-amber-700 dark:text-amber-400">
            Clause de non-responsabilité médicale
          </p>
          <p className="mt-1.5 text-sm leading-relaxed text-slate-600 dark:text-slate-400">
            {prediction.legal_disclaimer}
          </p>
          <p className="mt-2 font-mono text-xs text-slate-500">
            Statut : {prediction.status}
          </p>
        </div>
      </div>
    </section>
  );
}
