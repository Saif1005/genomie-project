/** Diagramme Harness — chemins de raisonnement + charge utile par flèche */
export const ANIMATED_ARCHITECTURE_MERMAID = `
flowchart TB
    classDef client fill:#881337,stroke:#f43f5e,color:#fff
    classDef host fill:#4c1d95,stroke:#7c3aed,color:#fff
    classDef agent fill:#0c4a6e,stroke:#0ea5e9,color:#e0f2fe
    classDef tool fill:#064e3b,stroke:#10b981,color:#d1fae5
    classDef pipe fill:#1e293b,stroke:#64748b,color:#f1f5f9

    subgraph HARNESS["Architecture multi-agents ZAYNB — serveur local"]
        direction TB

        subgraph CLIENT["① Couche Client"]
            UI["Interface / API / assistant"]:::client
        end

        subgraph HOST["② Orchestrateur"]
            MASTER["Graphe LangGraph<br/>planifier → exécuter"]:::host
        end

        subgraph AGENTS["③ Couche Agents"]
            direction LR
            AG1["DataManager"]:::agent
            AG2["VariantCalling"]:::agent
            AG3["VariantAnnotation"]:::agent
            AG4["VCFAnalysis"]:::agent
            AG5["Prediction"]:::agent
            AG6["ReportGenerator"]:::agent
        end

        subgraph TOOLS["④ Couche Outils"]
            direction TB
            T1["Stockage local"]:::tool
            T2["Parabricks / GATK4"]:::tool
            T3["ClinVar"]:::tool
            T4["QC clinique"]:::tool
            T5["Règles + BioGPT"]:::tool
            T6["Rapport JSON"]:::tool
        end
    end

    UI -->|"1. [Intention: analyser un patient]<br/>Flux: {patient_id, fastq_r1, fastq_r2}"| MASTER
    MASTER -->|"2. [Plan: FASTQ fournis]<br/>Flux: {fastq_r1, fastq_r2}"| AG1
    AG1 -->|"3. [FASTQ validés, appel de variants]<br/>Flux: {fastq_r1_uri, fastq_r2_uri}"| AG2
    AG2 -->|"4. [VCF filtré sur le panel]<br/>Flux: {vcf_uri}"| AG3
    AG3 -->|"5. [Allèles annotés ClinVar]<br/>Flux: {annotated_variants_path}"| AG4
    AG4 -->|"6. [Variants classés]<br/>Flux: {panel_analysis}"| AG5
    AG5 -->|"7. [Risque déterminé]<br/>Flux: {risk_level, rationale}"| AG6
    AG6 -.->|"8. [Objectif atteint, fin du plan]<br/>Flux: {clinical_report, report_uri}"| MASTER

    AG1 --> T1
    AG2 --> T2
    AG3 --> T3
    AG4 --> T4
    AG5 --> T5
    AG6 --> T6
`.trim();

export type NodePair = [from: string, to: string];

export type ArchWorkflowStep = {
  id: number;
  agent: string;
  title: string;
  payload: string;
  delegation: NodePair;
  tool?: NodePair;
  toolName?: string;
  /** Agent exécuté une seule fois (fine-tuning LoRA optionnel) */
  oneShot?: boolean;
  oneShotNode?: string;
  /** Retour rapport validé par l'orchestrateur avant livraison client */
  orchestratorValidation?: boolean;
  validationNode?: string;
};

/** Étapes du workflow — délégation + exécution tool externe */
export const ARCH_WORKFLOW: ArchWorkflowStep[] = [
  {
    id: 1,
    agent: 'Client UI',
    title: 'Intention — analyser un patient',
    payload: '{patient_id, fastq_r1, fastq_r2}',
    delegation: ['UI', 'MASTER'],
  },
  {
    id: 2,
    agent: 'DataManager',
    title: 'Validation et rangement des FASTQ',
    payload: '{fastq_r1, fastq_r2}',
    delegation: ['MASTER', 'AG1'],
    tool: ['AG1', 'T1'],
    toolName: 'Stockage local',
  },
  {
    id: 3,
    agent: 'VariantCalling',
    title: 'FASTQ → VCF filtré (panel, hg38)',
    payload: '{fastq_r1_uri, fastq_r2_uri}',
    delegation: ['AG1', 'AG2'],
    tool: ['AG2', 'T2'],
    toolName: 'Parabricks / GATK4',
  },
  {
    id: 4,
    agent: 'VariantAnnotation',
    title: 'Annotation ClinVar',
    payload: '{vcf_uri}',
    delegation: ['AG2', 'AG3'],
    tool: ['AG3', 'T3'],
    toolName: 'ClinVar',
  },
  {
    id: 5,
    agent: 'VCFAnalysis',
    title: 'Contrôle qualité et classification',
    payload: '{annotated_variants_path}',
    delegation: ['AG3', 'AG4'],
    tool: ['AG4', 'T4'],
    toolName: 'QC clinique',
  },
  {
    id: 6,
    agent: 'Prediction',
    title: 'Niveau de risque (règles) + commentaire BioGPT',
    payload: '{panel_analysis}',
    delegation: ['AG4', 'AG5'],
    tool: ['AG5', 'T5'],
    toolName: 'Règles + BioGPT',
  },
  {
    id: 7,
    agent: 'ReportGenerator',
    title: 'Rapport clinique reproductible',
    payload: '{risk_level, rationale}',
    delegation: ['AG5', 'AG6'],
    tool: ['AG6', 'T6'],
    toolName: 'Rapport JSON',
  },
  {
    id: 8,
    agent: 'Orchestrateur',
    title: 'Objectif atteint — fin du plan et retour client',
    payload: '{clinical_report, report_uri}',
    delegation: ['AG6', 'MASTER'],
    orchestratorValidation: true,
    validationNode: 'MASTER',
  },
];
