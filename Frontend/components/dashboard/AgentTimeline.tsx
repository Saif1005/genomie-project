import BarList from '@/components/charts/BarList';
import ChartCard from '@/components/charts/ChartCard';
import { stepLabel } from '@/lib/utils/stats';
import type { StepTiming } from '@/types/api';

function fmtSeconds(s: number): string {
  if (s < 1) return `${Math.round(s * 1000)} ms`;
  if (s < 60) return `${s.toFixed(1)} s`;
  return `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
}

/** Time spent by each agent of the orchestrator for this job (cached steps shown as 0 s). */
export default function AgentTimeline({ steps, total, router }: { steps: StepTiming[]; total?: number | null; router?: string | null }) {
  if (!steps.length) return null;
  const agentTime = steps.reduce((a, s) => a + (s.status === 'completed' ? s.duration : 0), 0);
  return (
    <ChartCard
      title="Agent execution timeline"
      subtitle={`Wall time ${total != null ? fmtSeconds(total) : '—'} · agents ${fmtSeconds(agentTime)} · orchestration overhead ${
        total != null ? fmtSeconds(Math.max(0, total - agentTime)) : '—'
      } · router ${router ?? '—'}`}
      table={{
        columns: ['Agent', 'Status', 'Duration (s)'],
        rows: steps.map((s) => [stepLabel(s.ui_step), s.status, s.duration.toFixed(3)]),
      }}
    >
      <BarList
        items={steps.map((s) => ({
          label: stepLabel(s.ui_step),
          value: s.status === 'completed' ? s.duration : 0,
          display: s.status === 'cached' ? 'cached result' : s.status === 'failed' ? 'failed' : fmtSeconds(s.duration),
          color: s.status === 'failed' ? 'var(--viz-critical)' : 'var(--viz-seq)',
          tooltip: [`${s.tool}`, `status: ${s.status}`, `duration: ${fmtSeconds(s.duration)}`],
        }))}
        labelWidth="9rem"
        valueColumn="7rem"
        ariaLabel="Duration of each agent"
      />
    </ChartCard>
  );
}
