/** Diagramme Mermaid — architecture multi-agents ZAYNB (serveur local) */
export const ARCHITECTURE_MERMAID = `
flowchart TB
    classDef client fill:#881337,stroke:#f43f5e,color:#fff,stroke-width:2px
    classDef host fill:#4c1d95,stroke:#7c3aed,color:#fff,stroke-width:2px
    classDef agent fill:#0c4a6e,stroke:#0ea5e9,color:#e0f2fe,stroke-width:2px
    classDef pipe fill:#1e293b,stroke:#64748b,color:#f1f5f9,stroke-width:2px

    subgraph HARNESS["Orchestrateur LangGraph — planifier → exécuter"]
        direction TB
        CLIENT["Interface / API<br/>patient_id + FASTQ ou VCF"]:::client
        MASTER["Planificateur dynamique<br/>registre d'outils (requires / produces)"]:::host
        subgraph AGENTS["Agents src/agents/"]
            direction LR
            AG1["DataManager"]:::agent
            AG2["VariantCalling<br/>Parabricks · GATK4"]:::agent
            AG3["VariantAnnotation<br/>ClinVar"]:::agent
            AG4["VCFAnalysis<br/>QC · classification"]:::agent
            AG5["Prediction<br/>règles + BioGPT"]:::agent
            AG6["ReportGenerator"]:::agent
        end
    end

    subgraph PIPELINE["Données — /data/zaynb (serveur local)"]
        direction LR
        S1["FASTQ"]:::pipe
        S2["BAM"]:::pipe
        S3["VCF filtré"]:::pipe
        S4["Variants annotés"]:::pipe
        S5["Analyse panel"]:::pipe
        S6["Rapport JSON"]:::pipe
    end

    CLIENT ==> MASTER
    MASTER ==> AG1 ==> AG2 ==> AG3 ==> AG4 ==> AG5 ==> AG6 ==> CLIENT
    S1 --> S2 --> S3 --> S4 --> S5 --> S6
`.trim();

export type EdgePair = [from: string, to: string];

export type WorkflowStep = {
  id: string;
  tag: string;
  title: string;
  description: string;
  dataLabel: string;
  transport: string;
  highlight: EdgePair[];
};

export const WORKFLOW_STEPS: WorkflowStep[] = [
  {
    id: 'client',
    tag: 'Entrée',
    title: 'Requête utilisateur',
    description: 'Interface, API REST ou assistant : patient_id et chemins FASTQ ou VCF sur le serveur.',
    dataLabel: 'patient_id · contexte',
    transport: 'Requête',
    highlight: [['CLIENT', 'MASTER']],
  },
  {
    id: 'master',
    tag: 'Planificateur',
    title: 'Plan dynamique',
    description:
      "Chaînage arrière depuis le rapport : un VCF fourni saute l'alignement, un résultat en cache est réutilisé.",
    dataLabel: 'Plan d’outils',
    transport: 'Contexte',
    highlight: [['MASTER', 'AG1']],
  },
  {
    id: 'ag1',
    tag: 'DataManager',
    title: 'Validation FASTQ',
    description: 'Contrôle des FASTQ R1/R2 et rangement dans patients/<ID>/input.',
    dataLabel: 'FASTQ R1/R2',
    transport: 'FASTQ',
    highlight: [['AG1', 'AG2']],
  },
  {
    id: 'ag2',
    tag: 'VariantCalling',
    title: 'BWA-MEM → BQSR → HaplotypeCaller',
    description:
      'Parabricks (GPU ≥ 16 Go) ou GATK4 (CPU), restreint au panel ; normalisation et filtres GATK ; reprise sur incident.',
    dataLabel: 'variants.vcf.gz',
    transport: 'VCF',
    highlight: [['AG2', 'AG3']],
  },
  {
    id: 'ag3',
    tag: 'VariantAnnotation',
    title: 'Annotation ClinVar',
    description: 'Lecture du VCF sur les régions du panel, annotation ClinVar locale (version tracée).',
    dataLabel: 'Variants annotés',
    transport: 'Annotations',
    highlight: [['AG3', 'AG4']],
  },
  {
    id: 'ag4',
    tag: 'VCFAnalysis',
    title: 'Contrôle qualité et classification',
    description: 'QUAL, profondeur, VAF selon la zygotie ; P/LP confirmés, à confirmer, VUS.',
    dataLabel: 'Analyse du panel',
    transport: 'Analyse',
    highlight: [['AG4', 'AG5']],
  },
  {
    id: 'ag5',
    tag: 'Prediction',
    title: 'Niveau de risque',
    description: 'Règles zaynb-rules-v1 (HIGH / MODERATE / INDETERMINATE / LOW) ; BioGPT commente sans décider.',
    dataLabel: 'Risque + justification',
    transport: 'Risque',
    highlight: [['AG5', 'AG6']],
  },
  {
    id: 'ag6',
    tag: 'ReportGenerator',
    title: 'Rapport clinique',
    description: 'Rapport JSON reproductible (empreinte du VCF, versions panel et ClinVar), archivé côté patient.',
    dataLabel: 'Rapport clinique',
    transport: 'Rapport',
    highlight: [['AG6', 'CLIENT']],
  },
];
