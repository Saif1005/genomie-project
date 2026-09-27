'use client';

import { useTooltip } from '@/components/charts/Tooltip';
import { binLabel, fmtInt } from '@/lib/utils/stats';
import type { HistogramBin } from '@/types/api';

interface HistogramProps {
  bins: HistogramBin[];
  /** Clinical threshold drawn at the lower edge of the matching bin */
  threshold?: { value: number; label: string };
  /** Shaded expected range (e.g. heterozygous VAF 0.25–0.75) */
  band?: { from: number; to: number; label: string };
  digits?: number;
  unit?: string;
  ariaLabel: string;
  height?: number;
}

function niceMax(v: number): number {
  if (v <= 5) return 5;
  const p = 10 ** Math.floor(Math.log10(v));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}

/** Column histogram (2 px gap, 4 px rounded cap), hairline grid, tooltip per bin. */
export default function Histogram({ bins, threshold, band, digits = 0, unit = '', ariaLabel, height = 160 }: HistogramProps) {
  const { ref, show, hide, node } = useTooltip();
  const total = bins.reduce((a, b) => a + b.count, 0);
  const top = niceMax(Math.max(1, ...bins.map((b) => b.count)));
  const ticks = [0, top / 2, top];
  const inBand = (b: HistogramBin) => band != null && b.lo >= band.from && (b.hi ?? Infinity) <= band.to + 1e-9;
  const thresholdIdx = threshold ? bins.findIndex((b) => b.lo >= threshold.value) : -1;

  return (
    <div ref={ref} className="relative" role="img" aria-label={ariaLabel}>
      <div className="flex gap-2">
        <div className="flex flex-col justify-between text-right text-[10px] tabular-nums text-slate-400" style={{ height }}>
          {[...ticks].reverse().map((t) => (
            <span key={t} className="-translate-y-1/2 leading-none first:translate-y-0 last:translate-y-0">
              {fmtInt(t)}
            </span>
          ))}
        </div>
        <div className="relative flex-1">
          {ticks.map((t) => (
            <div
              key={t}
              className="absolute inset-x-0 h-px"
              style={{ bottom: `${(t / top) * height}px`, background: 'var(--viz-grid)' }}
              aria-hidden
            />
          ))}
          <div className="relative flex items-end gap-[2px]" style={{ height }}>
            {bins.map((b, i) => {
              const h = (b.count / top) * height;
              const label = binLabel(b.lo, b.hi, digits);
              return (
                <div
                  key={i}
                  className="relative flex h-full flex-1 items-end justify-center"
                  style={{ background: inBand(b) ? 'var(--viz-band)' : undefined }}
                  onMouseMove={(e) => show(e, `${label}${unit}`, [`${fmtInt(b.count)} variant(s)`, total ? `${((b.count / total) * 100).toFixed(1)}% of total` : ''])}
                  onMouseLeave={hide}
                >
                  {i === thresholdIdx && (
                    <span className="absolute inset-y-0 left-[-1px] z-10 w-px bg-slate-500 dark:bg-slate-400" aria-hidden />
                  )}
                  <div
                    className="w-full max-w-[24px] rounded-t"
                    style={{ height: b.count ? Math.max(h, 2) : 0, background: 'var(--viz-seq)' }}
                  />
                </div>
              );
            })}
          </div>
          <div className="mt-1.5 flex gap-[2px]">
            {bins.map((b, i) => (
              <span key={i} className="flex-1 truncate text-center text-[9px] tabular-nums text-slate-400" title={binLabel(b.lo, b.hi, digits)}>
                {i % Math.ceil(bins.length / 7) === 0 ? binLabel(b.lo, b.hi, digits).split('–')[0] : ''}
              </span>
            ))}
          </div>
        </div>
      </div>
      {(threshold || band) && (
        <div className="mt-2 flex flex-wrap justify-end gap-x-4 gap-y-1 text-[11px] text-slate-500">
          {band && (
            <span className="flex items-center gap-1.5">
              <span className="inline-block h-3 w-3 rounded-sm" style={{ background: 'var(--viz-band)' }} aria-hidden />
              {band.label}
            </span>
          )}
          {threshold && (
            <span className="flex items-center gap-1.5">
              <span className="inline-block h-3 w-px bg-slate-500" aria-hidden />
              {threshold.label}
            </span>
          )}
        </div>
      )}
      {node}
    </div>
  );
}
