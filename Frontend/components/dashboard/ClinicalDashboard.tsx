import ClinicalInferenceCard from '@/components/dashboard/ClinicalInferenceCard';
import PathogenicVariantsTable from '@/components/dashboard/PathogenicVariantsTable';
import SystemMetricsCard from '@/components/dashboard/SystemMetricsCard';
import type { ClinicalReport } from '@/types/api';

interface ClinicalDashboardProps {
  report: ClinicalReport;
}

export default function ClinicalDashboard({ report }: ClinicalDashboardProps) {
  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <div className="h-px flex-1 bg-gradient-to-r from-transparent via-dna-500/50 to-transparent" />
        <h2 className="text-sm font-bold uppercase tracking-widest text-dna-600 dark:text-dna-400">
          Dashboard clinique
        </h2>
        <div className="h-px flex-1 bg-gradient-to-r from-transparent via-dna-500/50 to-transparent" />
      </div>

      <SystemMetricsCard
        metrics={report.system_metrics}
        patientId={report.patient_id}
        reportId={report.report_id}
        generatedAt={report.generated_at}
      />

      <PathogenicVariantsTable
        variants={report.genomic_findings.pathogenic_variants_detected}
        genes={report.genomic_findings.identified_pathogenic_genes}
      />

      {(report.genomic_findings.variants_to_confirm?.length ?? 0) > 0 && (
        <PathogenicVariantsTable
          title="Variants à confirmer (seconde technique)"
          variants={report.genomic_findings.variants_to_confirm ?? []}
          showQcReason
        />
      )}

      <ClinicalInferenceCard prediction={report.clinical_prediction} />
    </div>
  );
}
