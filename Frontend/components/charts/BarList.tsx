'use client';

import { useTooltip } from '@/components/charts/Tooltip';

export interface BarItem {
  label: string;
  value: number;
  color?: string;
  display?: string;
  tooltip?: string[];
  flag?: React.ReactNode;
}

interface BarListProps {
  items: BarItem[];
  max?: number;
  threshold?: { value: number; label: string };
  labelWidth?: string;
  /** Values in a right-hand column (instead of at the bar tip): useful when a threshold crosses the area */
  valueColumn?: string;
  ariaLabel: string;
}

/** Horizontal bars (≤ 24 px, 4 px rounded end, square base), value at the bar tip. */
export default function BarList({ items, max, threshold, labelWidth = '9.5rem', valueColumn, ariaLabel }: BarListProps) {
  const { ref, show, hide, node } = useTooltip();
  const top = max ?? Math.max(1, ...items.map((i) => i.value));
  return (
    <div ref={ref} className="relative" role="img" aria-label={ariaLabel}>
      <ul className="space-y-1.5">
        {items.map((it) => {
          const pct = Math.max(0, Math.min(1, it.value / top)) * 100;
          return (
            <li
              key={it.label}
              className="grid items-center gap-3"
              style={{ gridTemplateColumns: valueColumn ? `${labelWidth} 1fr ${valueColumn}` : `${labelWidth} 1fr` }}
              onMouseMove={(e) => show(e, it.label, it.tooltip ?? [it.display ?? String(it.value)])}
              onMouseLeave={hide}
              tabIndex={0}
              onFocus={(e) => show(e, it.label, it.tooltip ?? [it.display ?? String(it.value)])}
              onBlur={hide}
            >
              <span className="flex min-w-0 items-center gap-1.5 text-xs text-slate-600 dark:text-slate-300">
                {it.flag}
                <span className="truncate">{it.label}</span>
              </span>
              <div className="relative flex h-6 items-center">
                {threshold && (
                  <span
                    className="absolute inset-y-0 z-10 w-px bg-slate-400 dark:bg-slate-500"
                    style={{ left: `${(threshold.value / top) * 100}%` }}
                    aria-hidden
                  />
                )}
                <div
                  className="h-4 rounded-r transition-[width] duration-500"
                  style={{ width: `${pct}%`, minWidth: it.value > 0 ? 3 : 0, background: it.color ?? 'var(--viz-seq)' }}
                />
                {!valueColumn && (
                  <span className="ml-2 shrink-0 text-xs font-semibold tabular-nums text-slate-700 dark:text-slate-200">
                    {it.display ?? it.value}
                  </span>
                )}
              </div>
              {valueColumn && (
                <span className="text-right text-xs font-semibold tabular-nums text-slate-700 dark:text-slate-200">
                  {it.display ?? it.value}
                </span>
              )}
            </li>
          );
        })}
      </ul>
      {threshold && (
        <p className="mt-2 flex items-center justify-end gap-1.5 text-[11px] text-slate-500">
          <span className="inline-block h-3 w-px bg-slate-400" aria-hidden />
          {threshold.label}
        </p>
      )}
      {node}
    </div>
  );
}
