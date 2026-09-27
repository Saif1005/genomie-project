'use client';

import { ShieldAlert } from 'lucide-react';
import BarList from '@/components/charts/BarList';
import ChartCard from '@/components/charts/ChartCard';
import Histogram from '@/components/charts/Histogram';
import StackedBars from '@/components/charts/StackedBars';
import StatTile from '@/components/dashboard/StatTile';
import {
  CATEGORY_LABELS,
  CATEGORY_ORDER,
  GROUPS,
  GROUP_OF,
  binLabel,
  fmtInt,
  fmtNum,
  fmtPct,
} from '@/lib/utils/stats';
import type { Distribution, Reproducibility, VcfStatistics } from '@/types/api';

const TYPE_LABELS: Record<string, string> = { SNV: 'SNV', Deletion: 'Deletion', Insertion: 'Insertion', Complex: 'Complex' };

function describeRows(d: Distribution['summary']): (string | number)[][] {
  return [
    ['n', fmtInt(d.n)],
    ['Minimum', fmtNum(d.min)],
    ['Q1', fmtNum(d.q1)],
    ['Median', fmtNum(d.median)],
    ['Q3', fmtNum(d.q3)],
    ['Maximum', fmtNum(d.max)],
    ['Mean', fmtNum(d.mean)],
    ['Standard deviation', fmtNum(d.sd)],
  ];
}

function histTable(d: Distribution, digits = 0) {
  return {
    columns: ['Bin', 'Variants'],
    rows: [...d.histogram.map((b) => [binLabel(b.lo, b.hi, digits), b.count]), ['—', '—'], ...describeRows(d.summary)],
  };
}

function SummaryLine({ d, digits = 1 }: { d: Distribution['summary']; digits?: number }) {
  return (
    <p className="mt-3 text-[11px] tabular-nums text-slate-500 dark:text-slate-400">
      n = {fmtInt(d.n)} · median {fmtNum(d.median, digits)} · Q1–Q3 {fmtNum(d.q1, digits)}–{fmtNum(d.q3, digits)} · mean {fmtNum(d.mean, digits)} ± {fmtNum(d.sd, digits)}
    </p>
  );
}

export default function VcfStatisticsPanel({ stats, thresholds }: { stats: VcfStatistics; thresholds?: Reproducibility['qc_thresholds'] }) {
  const p = stats.panel;
  const d = stats.distributions;
  const check = (id: string) => stats.quality_checks.find((c) => c.id === id);
  const minDp = thresholds?.min_depth ?? 15;
  const minQual = thresholds?.min_qual ?? 30;

  const categoryItems = CATEGORY_ORDER.map((c) => ({
    label: CATEGORY_LABELS[c],
    value: p.by_category[c] ?? 0,
    color: GROUPS.find((g) => g.id === GROUP_OF[c])!.color,
    flag: GROUP_OF[c] === 'pathogenic' && (p.by_category[c] ?? 0) > 0 ? <ShieldAlert className="h-3.5 w-3.5 shrink-0" style={{ color: 'var(--viz-critical)' }} aria-label="pathogenic" /> : undefined,
  }));

  const typeItems = Object.entries(p.by_type).map(([k, v]) => ({ label: TYPE_LABELS[k] ?? k, value: v, display: `${v} (${fmtPct(v / Math.max(1, p.alleles_called), 0)})` }));
  const filterItems = Object.entries(p.by_filter).map(([k, v]) => ({ label: k, value: v, color: k === 'PASS' ? 'var(--viz-seq)' : 'var(--viz-series-2)' }));

  const geneRows = stats.per_gene.map((g) => ({
    label: g.gene,
    segments: GROUPS.map((grp) => ({
      id: grp.id,
      label: grp.label,
      color: grp.color,
      value: CATEGORY_ORDER.filter((c) => GROUP_OF[c] === grp.id).reduce((a, c) => a + (g.categories[c] ?? 0), 0),
    })),
  }));

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <StatTile label="Panel variants" value={fmtInt(p.variants_in_panel)} hint={`${fmtInt(p.alleles_called)} alleles carried`} />
        <StatTile label="PASS (GATK filters)" value={fmtPct(p.pass_rate)} hint={check('pass_rate')?.expected} status={check('pass_rate')?.status} />
        <StatTile label="Ti/Tv (PASS SNVs)" value={fmtNum(p.ti_tv)} hint={`${p.transitions_pass_snv} Ti / ${p.transversions_pass_snv} Tv`} status={check('ti_tv')?.status} />
        <StatTile label="Het / hom ratio" value={fmtNum(p.het_hom_ratio)} hint={check('het_hom')?.expected} status={check('het_hom')?.status} />
        <StatTile label="Median depth" value={`${fmtNum(d.depth.summary.median, Number.isInteger(d.depth.summary.median ?? 0) ? 0 : 1)}×`} hint={`clinical threshold ${minDp}×`} status={check('median_dp')?.status} />
        <StatTile label="ClinVar annotated" value={fmtPct(p.clinvar_annotated_rate)} hint={`${p.clinvar_annotated} / ${p.variants_in_panel}`} />
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <ChartCard
          title="ClinVar classification of panel variants"
          subtitle="Every germline variant in the 13 genes, benign included. Pathogenic variants carry the alert icon."
          table={{ columns: ['Category', 'Variants'], rows: categoryItems.map((i) => [i.label, i.value]) }}
        >
          <BarList items={categoryItems} labelWidth="12.5rem" ariaLabel="Number of variants per ClinVar category" />
        </ChartCard>

        <div className="grid gap-5">
          <ChartCard title="Variant types" table={{ columns: ['Type', 'Variants'], rows: typeItems.map((i) => [i.label, i.value]) }}>
            <BarList items={typeItems} labelWidth="6rem" ariaLabel="Distribution of variant types" />
          </ChartCard>
          <ChartCard
            title="GATK filters"
            subtitle="A filtered variant is still analysed: if pathogenic it is reported as “to confirm”."
            legend={[{ label: 'PASS', color: 'var(--viz-seq)' }, { label: 'Filtered', color: 'var(--viz-series-2)' }]}
            table={{ columns: ['Filter', 'Variants'], rows: filterItems.map((i) => [i.label, i.value]) }}
          >
            <BarList items={filterItems} labelWidth="8rem" ariaLabel="Variants per GATK filter" />
          </ChartCard>
        </div>
      </div>

      <div className="grid gap-5 md:grid-cols-2">
        <ChartCard title="Call quality (QUAL)" subtitle="Phred confidence that the variant exists." table={histTable(d.quality)}>
          <Histogram bins={d.quality.histogram} threshold={{ value: minQual, label: `clinical threshold QUAL ${minQual}` }} ariaLabel="QUAL distribution" />
          <SummaryLine d={d.quality.summary} />
        </ChartCard>
        <ChartCard title="Read depth (DP)" subtitle="Number of reads at the variant site." table={histTable(d.depth)}>
          <Histogram bins={d.depth.histogram} threshold={{ value: minDp, label: `clinical threshold ${minDp}×` }} unit="×" ariaLabel="Depth distribution" />
          <SummaryLine d={d.depth.summary} digits={1} />
        </ChartCard>
        <ChartCard
          title="Heterozygous allele fraction (VAF)"
          subtitle="A germline heterozygote is expected near 0.5; a low VAF suggests mosaicism or CHIP."
          table={histTable(d.vaf_heterozygous, 2)}
        >
          <Histogram
            bins={d.vaf_heterozygous.histogram}
            band={{ from: thresholds?.het_vaf_min ?? 0.25, to: thresholds?.het_vaf_max ?? 0.75, label: 'germline heterozygous range' }}
            digits={2}
            ariaLabel="Heterozygous VAF distribution"
          />
          <SummaryLine d={d.vaf_heterozygous.summary} digits={3} />
        </ChartCard>
        <ChartCard title="Genotype quality (GQ)" subtitle="Phred confidence in the assigned genotype (capped at 99)." table={histTable(d.genotype_quality)}>
          <Histogram bins={d.genotype_quality.histogram} ariaLabel="GQ distribution" />
          <SummaryLine d={d.genotype_quality.summary} digits={0} />
        </ChartCard>
      </div>

      <ChartCard
        title="Variants per panel gene"
        subtitle="Breakdown by ClinVar classification family; hover for details."
        legend={GROUPS.map((g) => ({ label: g.label, color: g.color }))}
        table={{
          columns: ['Gene', 'Variants', 'PASS', 'Clinical QC pass', 'SNVs', 'Indels', 'P/LP', 'VUS/conflicting', 'Median DP'],
          rows: stats.per_gene.map((g) => [
            g.gene,
            g.variants,
            g.pass,
            g.qc_pass,
            g.snv,
            g.indel,
            g.categories.pathogenic_confirmed + g.categories.pathogenic_to_confirm + g.categories.lof_to_confirm,
            g.categories.vus + g.categories.conflicting,
            fmtNum(g.median_dp, 0),
          ]),
        }}
      >
        <StackedBars rows={geneRows} ariaLabel="Variants per gene and classification family" />
      </ChartCard>

      {stats.file && (
        <p className="text-xs text-slate-500 dark:text-slate-400">
          Whole VCF: {fmtInt(stats.file.records_read)} records, {fmtInt(stats.file.alleles_called)} alleles carried, Ti/Tv {fmtNum(stats.file.ti_tv)},
          het/hom {fmtNum(stats.file.het_hom_ratio)}, {fmtPct(stats.file.pass_rate)} PASS · method {stats.version} (type-7 quantiles, fixed bins).
        </p>
      )}
    </div>
  );
}
