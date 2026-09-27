'use client';

import { useCallback, useRef, useState } from 'react';

export interface TooltipState {
  x: number;
  y: number;
  title: string;
  lines: string[];
}

/** Tooltip positioned inside a `relative` container; follows the mouse and stays within bounds. */
export function useTooltip() {
  const ref = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<TooltipState | null>(null);

  const show = useCallback((e: React.MouseEvent | React.FocusEvent, title: string, lines: string[]) => {
    const box = ref.current?.getBoundingClientRect();
    if (!box) return;
    let x: number;
    let y: number;
    if ('clientX' in e) {
      x = e.clientX - box.left;
      y = e.clientY - box.top;
    } else {
      const t = (e.target as HTMLElement).getBoundingClientRect();
      x = t.left - box.left + t.width / 2;
      y = t.top - box.top;
    }
    setTip({ x, y, title, lines });
  }, []);
  const hide = useCallback(() => setTip(null), []);

  const node = tip ? (
    <div
      role="tooltip"
      className="pointer-events-none absolute z-20 min-w-[140px] max-w-[260px] rounded-lg border border-slate-200 bg-white/95 px-3 py-2 text-xs shadow-lg backdrop-blur dark:border-slate-700 dark:bg-slate-900/95"
      style={{
        left: Math.min(Math.max(tip.x, 70), (ref.current?.clientWidth ?? 300) - 70),
        top: tip.y - 12,
        transform: 'translate(-50%, -100%)',
      }}
    >
      <p className="font-semibold text-slate-900 dark:text-white">{tip.title}</p>
      {tip.lines.filter(Boolean).map((l) => (
        <p key={l} className="tabular-nums text-slate-600 dark:text-slate-300">
          {l}
        </p>
      ))}
    </div>
  ) : null;

  return { ref, show, hide, node };
}
