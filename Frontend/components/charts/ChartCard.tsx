'use client';

import { useState } from 'react';
import { BarChart3, Table2 } from 'lucide-react';

export interface TableView {
  columns: string[];
  rows: (string | number)[][];
}

interface ChartCardProps {
  title: string;
  subtitle?: string;
  legend?: { label: string; color: string }[];
  table?: TableView;
  className?: string;
  children: React.ReactNode;
}

/** Chart card: title, legend (≥ 2 series), chart ⇄ table toggle (accessibility). */
export default function ChartCard({ title, subtitle, legend, table, className = '', children }: ChartCardProps) {
  const [showTable, setShowTable] = useState(false);
  return (
    <section
      className={`rounded-2xl border border-slate-200 bg-white p-5 shadow-card dark:border-slate-800 dark:bg-slate-900 dark:shadow-card-dark ${className}`}
    >
      <header className="mb-4 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-bold text-slate-900 dark:text-white">{title}</h3>
          {subtitle && <p className="mt-0.5 text-xs leading-relaxed text-slate-500 dark:text-slate-400">{subtitle}</p>}
        </div>
        {table && (
          <button
            type="button"
            onClick={() => setShowTable((v) => !v)}
            className="flex shrink-0 items-center gap-1.5 rounded-lg border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-600 transition hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
            aria-pressed={showTable}
          >
            {showTable ? <BarChart3 className="h-3.5 w-3.5" /> : <Table2 className="h-3.5 w-3.5" />}
            {showTable ? 'Chart' : 'Table'}
          </button>
        )}
      </header>

      {legend && legend.length > 1 && !showTable && (
        <ul className="mb-4 flex flex-wrap gap-x-4 gap-y-1.5">
          {legend.map((l) => (
            <li key={l.label} className="flex items-center gap-1.5 text-xs text-slate-600 dark:text-slate-300">
              <span className="h-2.5 w-2.5 rounded-sm" style={{ background: l.color }} aria-hidden />
              {l.label}
            </li>
          ))}
        </ul>
      )}

      {showTable && table ? (
        <div className="max-h-80 overflow-auto rounded-lg border border-slate-200 dark:border-slate-700">
          <table className="w-full text-left text-xs">
            <thead className="sticky top-0 bg-slate-50 dark:bg-slate-800">
              <tr>
                {table.columns.map((c) => (
                  <th key={c} className="px-3 py-2 font-semibold text-slate-600 dark:text-slate-300">
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {table.rows.map((r, i) => (
                <tr key={i}>
                  {r.map((cell, j) => (
                    <td key={j} className={`px-3 py-1.5 text-slate-700 dark:text-slate-300 ${j > 0 ? 'tabular-nums' : ''}`}>
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        children
      )}
    </section>
  );
}
