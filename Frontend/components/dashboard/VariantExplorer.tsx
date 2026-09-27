'use client';

import { useMemo, useState } from 'react';
import { ArrowDownUp, Search, ShieldAlert } from 'lucide-react';
import { CATEGORY_LABELS, GROUPS, GROUP_OF, fmtNum, variantKey, type CategoryGroup } from '@/lib/utils/stats';
import type { VariantRow } from '@/types/api';

type SortKey = 'position' | 'gene' | 'quality' | 'dp' | 'vaf' | 'category';

const GROUP_RANK: Record<CategoryGroup, number> = { pathogenic: 0, uncertain: 1, unclassified: 2, benign: 3 };

export default function VariantExplorer({ variants }: { variants: VariantRow[] }) {
  const [groups, setGroups] = useState<Set<CategoryGroup>>(new Set(['pathogenic', 'uncertain', 'unclassified', 'benign']));
  const [gene, setGene] = useState('');
  const [query, setQuery] = useState('');
  const [onlyQcFail, setOnlyQcFail] = useState(false);
  const [sort, setSort] = useState<{ key: SortKey; asc: boolean }>({ key: 'category', asc: true });

  const genes = useMemo(() => Array.from(new Set(variants.map((v) => v.gene))).sort(), [variants]);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    const out = variants.filter(
      (v) =>
        groups.has(GROUP_OF[v.category]) &&
        (!gene || v.gene === gene) &&
        (!onlyQcFail || v.qc_status !== 'PASS') &&
        (!q || [v.hgvs, v.rsid, v.conditions, variantKey(v), v.gene].some((f) => f?.toLowerCase().includes(q))),
    );
    const val = (v: VariantRow): number | string => {
      switch (sort.key) {
        case 'gene': return v.gene;
        case 'quality': return v.quality ?? -1;
        case 'dp': return v.dp ?? -1;
        case 'vaf': return v.vaf ?? -1;
        case 'category': return GROUP_RANK[GROUP_OF[v.category]] * 1e10 + v.position;
        default: return v.position;
      }
    };
    return out.sort((a, b) => {
      const x = val(a), y = val(b);
      const c = typeof x === 'string' ? x.localeCompare(y as string) : (x as number) - (y as number);
      return sort.asc ? c : -c;
    });
  }, [variants, groups, gene, query, onlyQcFail, sort]);

  const toggle = (g: CategoryGroup) =>
    setGroups((prev) => {
      const next = new Set(prev);
      if (next.has(g)) next.delete(g);
      else next.add(g);
      return next;
    });

  const Th = ({ k, children, right }: { k: SortKey; children: React.ReactNode; right?: boolean }) => (
    <th className={`px-3 py-2 font-semibold ${right ? 'text-right' : ''}`}>
      <button
        type="button"
        onClick={() => setSort((s) => ({ key: k, asc: s.key === k ? !s.asc : true }))}
        className="inline-flex items-center gap-1 hover:text-slate-900 dark:hover:text-white"
      >
        {children}
        <ArrowDownUp className={`h-3 w-3 ${sort.key === k ? 'opacity-100' : 'opacity-30'}`} />
      </button>
    </th>
  );

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-card dark:border-slate-800 dark:bg-slate-900 dark:shadow-card-dark">
      <header className="mb-4">
        <h3 className="text-sm font-bold text-slate-900 dark:text-white">Variant explorer</h3>
        <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
          Every germline panel variant, with its ClinVar classification and clinical quality control.
        </p>
      </header>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        {GROUPS.map((g) => (
          <button
            key={g.id}
            type="button"
            onClick={() => toggle(g.id)}
            aria-pressed={groups.has(g.id)}
            className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition ${
              groups.has(g.id)
                ? 'border-slate-300 bg-slate-100 text-slate-800 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100'
                : 'border-slate-200 text-slate-400 dark:border-slate-800'
            }`}
          >
            <span className="h-2.5 w-2.5 rounded-sm" style={{ background: g.color, opacity: groups.has(g.id) ? 1 : 0.35 }} aria-hidden />
            {g.label} ({variants.filter((v) => GROUP_OF[v.category] === g.id).length})
          </button>
        ))}
        <select
          value={gene}
          onChange={(e) => setGene(e.target.value)}
          className="rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-xs dark:border-slate-700 dark:bg-slate-900"
          aria-label="Filter by gene"
        >
          <option value="">All genes</option>
          {genes.map((g) => (
            <option key={g} value={g}>{g}</option>
          ))}
        </select>
        <label className="flex items-center gap-1.5 text-xs text-slate-600 dark:text-slate-300">
          <input type="checkbox" checked={onlyQcFail} onChange={(e) => setOnlyQcFail(e.target.checked)} className="accent-dna-600" />
          Clinical QC failed only
        </label>
        <div className="relative ml-auto">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="HGVS, rsID, condition, position…"
            className="w-64 rounded-lg border border-slate-200 bg-white py-1.5 pl-8 pr-3 text-xs dark:border-slate-700 dark:bg-slate-900"
          />
        </div>
      </div>

      <p className="mb-2 text-xs text-slate-500">Showing {rows.length} of {variants.length} variants</p>
      <div className="max-h-[560px] overflow-auto rounded-lg border border-slate-200 dark:border-slate-700">
        <table className="w-full min-w-[980px] text-left text-xs">
          <thead className="sticky top-0 z-10 bg-slate-50 text-slate-600 dark:bg-slate-800 dark:text-slate-300">
            <tr>
              <Th k="category">Classification</Th>
              <Th k="gene">Gene</Th>
              <Th k="position">Position (hg38)</Th>
              <th className="px-3 py-2 font-semibold">Alleles</th>
              <th className="px-3 py-2 font-semibold">GT</th>
              <Th k="quality" right>QUAL</Th>
              <Th k="dp" right>DP</Th>
              <Th k="vaf" right>VAF</Th>
              <th className="px-3 py-2 font-semibold">FILTER / QC</th>
              <th className="px-3 py-2 font-semibold">ClinVar</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {rows.map((v) => {
              const grp = GROUPS.find((g) => g.id === GROUP_OF[v.category])!;
              return (
                <tr key={variantKey(v)} className="align-top hover:bg-slate-50 dark:hover:bg-slate-800/40">
                  <td className="px-3 py-2">
                    <span className="flex items-center gap-1.5 font-medium text-slate-800 dark:text-slate-100">
                      {grp.id === 'pathogenic' ? (
                        <ShieldAlert className="h-3.5 w-3.5 shrink-0" style={{ color: 'var(--viz-critical)' }} aria-hidden />
                      ) : (
                        <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: grp.color }} aria-hidden />
                      )}
                      {CATEGORY_LABELS[v.category]}
                    </span>
                  </td>
                  <td className="px-3 py-2 font-mono font-semibold">{v.gene}</td>
                  <td className="px-3 py-2 font-mono tabular-nums">{v.chromosome}:{v.position.toLocaleString('en-US')}</td>
                  <td className="max-w-[140px] break-all px-3 py-2 font-mono">{v.ref}&gt;{v.alt}<span className="block text-[10px] text-slate-400">{v.variant_type}</span></td>
                  <td className="px-3 py-2 font-mono">{v.genotype}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{fmtNum(v.quality, 1)}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{v.dp ?? '—'}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{fmtNum(v.vaf, 3)}</td>
                  <td className="px-3 py-2">
                    <span className={v.filter === 'PASS' ? 'text-slate-600 dark:text-slate-300' : 'font-medium text-slate-800 dark:text-slate-100'}>{v.filter}</span>
                    {v.qc_status !== 'PASS' && (
                      <span className="mt-0.5 block text-[10px] text-slate-500">{v.qc_flags.join(' · ')}</span>
                    )}
                  </td>
                  <td className="max-w-[260px] px-3 py-2">
                    {v.clinvar_significance ? (
                      <>
                        <span className="font-medium">{v.clinvar_significance.replace(/_/g, ' ')}</span>
                        {v.review_stars != null && <span className="ml-1 text-slate-400">{'★'.repeat(v.review_stars)}{'☆'.repeat(4 - v.review_stars)}</span>}
                        <span className="block truncate text-[10px] text-slate-500" title={v.conditions ?? ''}>
                          {[v.rsid, v.variation_id && `VCV ${v.variation_id}`].filter(Boolean).join(' · ')}
                          {v.conditions ? ` — ${v.conditions}` : ''}
                        </span>
                      </>
                    ) : (
                      <span className="text-slate-400">not listed</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
