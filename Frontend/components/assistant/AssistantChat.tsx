'use client';

import { Bot, Loader2, Send, Sparkles, User } from 'lucide-react';
import { useCallback, useRef, useState } from 'react';
import {
  formatApiError,
  isFastqFile,
  PATIENT_ID_PATTERN,
  sendAssistantMessage,
  uploadAndAnalyze,
} from '@/lib/api';
import type { ChatMessage } from '@/types/api';

interface AssistantChatProps {
  activeJobId: string | null;
  patientId: string | null;
  pendingR1: File | null;
  pendingR2: File | null;
  onJobStarted: (jobId: string, patientId: string) => void;
  disabled?: boolean;
}

export default function AssistantChat({
  activeJobId,
  patientId,
  pendingR1,
  pendingR2,
  onJobStarted,
  disabled,
}: AssistantChatProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      role: 'assistant',
      content:
        'Bonjour. Je suis l\'assistant Zaynb (multi-agents). Vous pouvez me demander de lancer une analyse, expliquer le pipeline, ou consulter un job — en langage naturel.',
    },
  ]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  const scrollDown = () => {
    requestAnimationFrame(() => bottomRef.current?.scrollIntoView({ behavior: 'smooth' }));
  };

  const tryLaunchWithFiles = useCallback(
    async (pid: string): Promise<string | null> => {
      if (!pendingR1 || !pendingR2 || !isFastqFile(pendingR1) || !isFastqFile(pendingR2)) {
        return null;
      }
      const res = await uploadAndAnalyze({
        patient_id: pid,
        fastq_r1: pendingR1,
        fastq_r2: pendingR2,
      });
      onJobStarted(res.job_id, res.patient_id);
      return res.job_id;
    },
    [pendingR1, pendingR2, onJobStarted],
  );

  const handleSend = async (e: React.FormEvent) => {
    e.preventDefault();
    const text = input.trim();
    if (!text || loading || disabled) return;

    const userMsg: ChatMessage = { role: 'user', content: text };
    const nextHistory = [...messages, userMsg];
    setMessages(nextHistory);
    setInput('');
    setLoading(true);
    scrollDown();

    try {
      const hasPendingUpload = Boolean(pendingR1 && pendingR2);
      const res = await sendAssistantMessage({
        message: text,
        history: messages.filter((m) => m.role !== 'system'),
        context: {
          job_id: activeJobId,
          patient_id: patientId,
          pending_upload: hasPendingUpload,
        },
      });

      let reply = res.reply;
      let launchedJobId = res.job_id;

      if (
        res.intent === 'start_fastq' &&
        res.missing_fields.some((f) => f.startsWith('fastq_r')) &&
        hasPendingUpload
      ) {
        const pid = res.patient_id || patientId || extractPatientId(text);
        if (pid && PATIENT_ID_PATTERN.test(pid)) {
          try {
            const uploadedJobId = await tryLaunchWithFiles(pid);
            if (uploadedJobId) {
              launchedJobId = uploadedJobId;
              reply = `Fichiers FASTQ attachés détectés — workflow lancé (job ${uploadedJobId}).`;
            }
          } catch {
            reply =
              'Échec du lancement avec les fichiers attachés. Réessayez via l\'onglet Upload.';
          }
        }
      }

      if (launchedJobId && (res.intent === 'start_fastq' || res.action_taken === 'started_fastq')) {
        onJobStarted(
          launchedJobId,
          res.patient_id || patientId || extractPatientId(text) || '',
        );
      } else if (res.job_id && res.action_taken?.startsWith('started')) {
        onJobStarted(res.job_id, res.patient_id || patientId || '');
      }

      setMessages((prev) => [...prev, { role: 'assistant', content: reply }]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: formatApiError(err),
        },
      ]);
    } finally {
      setLoading(false);
      scrollDown();
    }
  };

  return (
    <section className="flex h-full min-h-[420px] flex-col rounded-2xl border border-slate-200 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900 dark:shadow-card-dark">
      <header className="flex items-center gap-3 border-b border-slate-200 px-5 py-4 dark:border-slate-800">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-gradient-to-br from-dna-500 to-sky-600 text-white">
          <Bot className="h-5 w-5" />
        </div>
        <div>
          <h2 className="text-sm font-bold text-slate-900 dark:text-white">
            Assistant IA multi-agents
          </h2>
          <p className="flex items-center gap-1 text-xs text-slate-500">
            <Sparkles className="h-3 w-3 text-dna-500" />
            Compréhension du langage naturel (Mistral)
          </p>
        </div>
      </header>

      <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4 sm:px-5">
        {messages.map((m, i) => (
          <div
            key={i}
            className={`flex gap-2 ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            {m.role === 'assistant' && (
              <Bot className="mt-1 h-4 w-4 shrink-0 text-dna-500" />
            )}
            <div
              className={`max-w-[90%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
                m.role === 'user'
                  ? 'bg-dna-600 text-white'
                  : 'border border-slate-200 bg-slate-50 text-slate-800 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200'
              }`}
            >
              {m.content}
            </div>
            {m.role === 'user' && (
              <User className="mt-1 h-4 w-4 shrink-0 text-slate-400" />
            )}
          </div>
        ))}
        {loading && (
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <Loader2 className="h-4 w-4 animate-spin text-dna-500" />
            Analyse de votre demande…
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <form
        onSubmit={handleSend}
        className="border-t border-slate-200 p-4 dark:border-slate-800"
      >
        <div className="flex gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ex. : Lance l'analyse pour PATIENT001…"
            disabled={disabled || loading}
            className="flex-1 rounded-xl border border-slate-300 bg-slate-50 px-4 py-2.5 text-sm outline-none focus:border-dna-500 dark:border-slate-600 dark:bg-slate-800 dark:text-white"
          />
          <button
            type="submit"
            disabled={disabled || loading || !input.trim()}
            className="inline-flex h-11 w-11 items-center justify-center rounded-xl bg-dna-600 text-white transition hover:bg-dna-500 disabled:opacity-50"
          >
            <Send className="h-4 w-4" />
          </button>
        </div>
        {pendingR1 && pendingR2 && (
          <p className="mt-2 text-xs text-dna-600 dark:text-dna-400">
            FASTQ attachés : {pendingR1.name}, {pendingR2.name} — dites « lance l&apos;analyse pour PATIENT… »
          </p>
        )}
      </form>
    </section>
  );
}

function extractPatientId(text: string): string | null {
  const m = text.match(/\b(PATIENT\d+|[A-Za-z][A-Za-z0-9_\-]{2,31})\b/);
  return m ? m[1] : null;
}
