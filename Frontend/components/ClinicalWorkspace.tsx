'use client';

import { useCallback, useState } from 'react';
import AnalysisForm from '@/components/analysis/AnalysisForm';
import ExecutionMonitor from '@/components/analysis/ExecutionMonitor';
import FastqUploadForm from '@/components/analysis/FastqUploadForm';
import AssistantChat from '@/components/assistant/AssistantChat';
import ClinicalDashboard from '@/components/dashboard/ClinicalDashboard';
import { useJobPolling } from '@/lib/hooks/useJobPolling';

type InputTab = 'upload' | 's3';

export default function ClinicalWorkspace() {
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [activePatientId, setActivePatientId] = useState<string | null>(null);
  const [inputTab, setInputTab] = useState<InputTab>('upload');
  const [pendingR1, setPendingR1] = useState<File | null>(null);
  const [pendingR2, setPendingR2] = useState<File | null>(null);

  const { data: job, isLoading, mutate } = useJobPolling(activeJobId);

  const handleJobStarted = useCallback((jobId: string, patientId: string) => {
    setActiveJobId(jobId);
    setActivePatientId(patientId);
    void mutate();
  }, [mutate]);

  const handleNewAnalysis = () => {
    setActiveJobId(null);
    setActivePatientId(null);
    setPendingR1(null);
    setPendingR2(null);
  };

  const isRunning =
    activeJobId &&
    job &&
    (job.status === 'queued' || job.status === 'running');

  const showDashboard = job?.status === 'completed' && job.result != null;

  return (
    <div className="space-y-8">
      <section>
        <p className="text-sm font-medium text-dna-600 dark:text-dna-400">
          Système multi-agents · Assistant IA · Upload FASTQ
        </p>
        <h1 className="mt-1 text-2xl font-bold tracking-tight text-slate-900 dark:text-white sm:text-3xl">
          Plateforme génomique Zaynb
        </h1>
        <p className="mt-2 max-w-3xl text-sm leading-relaxed text-slate-600 dark:text-slate-400">
          Attachez vos FASTQ, parlez à l&apos;assistant en langage naturel, ou saisissez des chemins sur le serveur.
          L&apos;orchestrateur enchaîne Parabricks GATK, analyse VCF et inférence BioGPT.
        </p>
      </section>

      {!activeJobId && (
        <div className="grid gap-6 lg:grid-cols-5">
          <div className="lg:col-span-2">
            <AssistantChat
              activeJobId={activeJobId}
              patientId={activePatientId}
              pendingR1={pendingR1}
              pendingR2={pendingR2}
              onJobStarted={handleJobStarted}
            />
          </div>

          <div className="space-y-4 lg:col-span-3">
            <div className="flex gap-2 rounded-xl border border-slate-200 bg-slate-100 p-1 dark:border-slate-800 dark:bg-slate-900">
              <TabButton
                active={inputTab === 'upload'}
                onClick={() => setInputTab('upload')}
              >
                Fichiers FASTQ
              </TabButton>
              <TabButton
                active={inputTab === 's3'}
                onClick={() => setInputTab('s3')}
              >
                Chemins serveur
              </TabButton>
            </div>

            {inputTab === 'upload' ? (
              <FastqUploadForm
                onJobStarted={handleJobStarted}
                onFilesSelected={(a, b) => {
                  setPendingR1(a);
                  setPendingR2(b);
                }}
              />
            ) : (
              <AnalysisForm onJobStarted={handleJobStarted} />
            )}
          </div>
        </div>
      )}

      {activeJobId && (
        <>
          <ExecutionMonitor jobId={activeJobId} job={job} isLoading={isLoading} />

          {showDashboard && <ClinicalDashboard report={job.result!} />}

          {(job?.status === 'completed' || job?.status === 'failed') && (
            <div className="flex justify-center pt-2">
              <button
                type="button"
                onClick={handleNewAnalysis}
                className="rounded-lg border border-dna-500/40 bg-dna-500/10 px-5 py-2.5 text-sm font-semibold text-dna-700 dark:text-dna-400"
              >
                Nouvelle analyse
              </button>
            </div>
          )}
        </>
      )}

      {isRunning && (
        <p className="text-center text-xs text-slate-500 animate-pulseDNA">
          Polling actif — mise à jour toutes les 4 secondes
        </p>
      )}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex-1 rounded-lg px-4 py-2 text-sm font-semibold transition ${
        active
          ? 'bg-white text-dna-700 shadow-sm dark:bg-slate-800 dark:text-dna-400'
          : 'text-slate-600 hover:text-slate-900 dark:text-slate-400'
      }`}
    >
      {children}
    </button>
  );
}
