'use client';

import { useTooltip } from '@/components/charts/Tooltip';

export interface StackRow {
  label: string;
  segments: { id: string; label: string; value: number; color: string }[];
  suffix?: React.ReactNode;
}

interface StackedBarsProps {
  rows: StackRow[];
  ariaLabel: string;
  labelWidth?: string;
}

/** Horizontal stacked bars: 2 px surface-coloured gap between segments, total at the end. */
export default function StackedBars({ rows, ariaLabel, labelWidth = '4.5rem' }: StackedBarsProps) {
  const { ref, show, hide, node } = useTooltip();
  const top = Math.max(1, ...rows.map((r) => r.segments.reduce((a, s) => a + s.value, 0)));
  return (
    <div ref={ref} className="relative" role="img" aria-label={ariaLabel}>
      <ul className="space-y-1.5">
        {rows.map((r) => {
          const total = r.segments.reduce((a, s) => a + s.value, 0);
          const lines = r.segments.filter((s) => s.value > 0).map((s) => `${s.label}: ${s.value}`);
          return (
            <li
              key={r.label}
              className="grid items-center gap-3"
              style={{ gridTemplateColumns: `${labelWidth} 1fr` }}
              onMouseMove={(e) => show(e, `${r.label} — ${total} variant(s)`, lines.length ? lines : ['no variant'])}
              onMouseLeave={hide}
            >
              <span className="font-mono text-xs font-semibold text-slate-700 dark:text-slate-200">{r.label}</span>
              <div className="flex h-6 items-center">
                <div className="flex h-4 gap-[2px]" style={{ width: `${(total / top) * 100}%` }}>
                  {r.segments
                    .filter((s) => s.value > 0)
                    .map((s, i, arr) => (
                      <div
                        key={s.id}
                        className={i === arr.length - 1 ? 'rounded-r' : ''}
                        style={{ flexGrow: s.value, flexBasis: 0, minWidth: 3, background: s.color }}
                      />
                    ))}
                </div>
                <span className="ml-2 shrink-0 text-xs font-semibold tabular-nums text-slate-700 dark:text-slate-200">{total}</span>
                {r.suffix}
              </div>
            </li>
          );
        })}
      </ul>
      {node}
    </div>
  );
}
