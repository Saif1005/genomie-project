import { AlertTriangle, CheckCircle2, MinusCircle } from 'lucide-react';

interface StatTileProps {
  label: string;
  value: string;
  hint?: string;
  status?: 'OK' | 'WARN' | 'NA';
}

/** Stat tile: label, value, expected range and status (icon + text, never colour alone). */
export default function StatTile({ label, value, hint, status }: StatTileProps) {
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-4 dark:border-slate-800 dark:bg-slate-800/40">
      <p className="text-xs font-medium text-slate-500 dark:text-slate-400">{label}</p>
      <p className="mt-1 text-2xl font-semibold tracking-tight text-slate-900 dark:text-white">{value}</p>
      {(hint || status) && (
        <p className="mt-1.5 flex items-center gap-1.5 text-[11px] text-slate-500 dark:text-slate-400">
          {status === 'OK' && <CheckCircle2 className="h-3.5 w-3.5 shrink-0" style={{ color: 'var(--viz-good)' }} aria-label="compliant" />}
          {status === 'WARN' && <AlertTriangle className="h-3.5 w-3.5 shrink-0" style={{ color: 'var(--viz-warning)' }} aria-label="warning" />}
          {status === 'NA' && <MinusCircle className="h-3.5 w-3.5 shrink-0 text-slate-400" aria-label="not assessable" />}
          <span>{hint}</span>
        </p>
      )}
    </div>
  );
}

export function StatusPill({ status }: { status: 'OK' | 'WARN' | 'NA' }) {
  const map = {
    OK: { label: 'Pass', icon: CheckCircle2, color: 'var(--viz-good)' },
    WARN: { label: 'Warning', icon: AlertTriangle, color: 'var(--viz-warning)' },
    NA: { label: 'Not assessable', icon: MinusCircle, color: '#94a3b8' },
  }[status];
  const Icon = map.icon;
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-slate-200 px-2 py-0.5 text-[11px] font-semibold text-slate-700 dark:border-slate-700 dark:text-slate-200">
      <Icon className="h-3.5 w-3.5" style={{ color: map.color }} aria-hidden />
      {map.label}
    </span>
  );
}
