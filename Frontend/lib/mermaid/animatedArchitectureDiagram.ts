/** Harness diagram — reasoning paths + payload per arrow */
export const ANIMATED_ARCHITECTURE_MERMAID = `
flowchart TB
    classDef client fill:#881337,stroke:#f43f5e,color:#fff
    classDef host fill:#4c1d95,stroke:#7c3aed,color:#fff
    classDef agent fill:#0c4a6e,stroke:#0ea5e9,color:#e0f2fe
    classDef tool fill:#064e3b,stroke:#10b981,color:#d1fae5
    classDef pipe fill:#1e293b,stroke:#64748b,color:#f1f5f9

    subgraph HARNESS["GermlineIQ multi-agent architecture — local server"]
        direction TB

        subgraph CLIENT["① Client layer"]
            UI["Interface / API / assistant"]:::client
        end

        subgraph HOST["② Orchestrator"]
            MASTER["LangGraph graph<br/>plan → execute"]:::host
        end

        subgraph AGENTS["③ Agent layer"]
            direction LR
            AG1["DataManager"]:::agent
            AG2["VariantCalling"]:::agent
            AG3["VariantAnnotation"]:::agent
            AG4["VCFAnalysis"]:::agent
            AG5["Prediction"]:::agent
            AG6["ReportGenerator"]:::agent
        end

        subgraph TOOLS["④ Tool layer"]
            direction TB
            T1["Local storage"]:::tool
            T2["Parabricks / GATK4"]:::tool
            T3["ClinVar"]:::tool
            T4["Clinical QC + statistics"]:::tool
            T5["Rules + verified BioGPT"]:::tool
            T6["JSON report"]:::tool
        end
    end

    UI -->|"1. [Intent: analyse a patient]<br/>Flow: {patient_id, fastq_r1, fastq_r2}"| MASTER
    MASTER -->|"2. [Plan: FASTQ provided]<br/>Flow: {fastq_r1, fastq_r2}"| AG1
    AG1 -->|"3. [FASTQ validated, variant calling]<br/>Flow: {fastq_r1_uri, fastq_r2_uri}"| AG2
    AG2 -->|"4. [VCF filtered on the panel]<br/>Flow: {vcf_uri, alignment_qc}"| AG3
    AG3 -->|"5. [ClinVar-annotated alleles]<br/>Flow: {annotated_variants_path}"| AG4
    AG4 -->|"6. [Classified variants]<br/>Flow: {panel_analysis, vcf_statistics}"| AG5
    AG5 -->|"7. [Risk determined]<br/>Flow: {risk_level, rationale}"| AG6
    AG6 -.->|"8. [Goal reached, end of plan]<br/>Flow: {clinical_report, report_uri}"| MASTER

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
  /** Agent run only once (optional LoRA fine-tuning) */
  oneShot?: boolean;
  oneShotNode?: string;
  /** Report returned and validated by the orchestrator before delivery */
  orchestratorValidation?: boolean;
  validationNode?: string;
};

/** Workflow steps — delegation + external tool execution */
export const ARCH_WORKFLOW: ArchWorkflowStep[] = [
  {
    id: 1,
    agent: 'Client UI',
    title: 'Intent — analyse a patient',
    payload: '{patient_id, fastq_r1, fastq_r2}',
    delegation: ['UI', 'MASTER'],
  },
  {
    id: 2,
    agent: 'DataManager',
    title: 'FASTQ validation and storage',
    payload: '{fastq_r1, fastq_r2}',
    delegation: ['MASTER', 'AG1'],
    tool: ['AG1', 'T1'],
    toolName: 'Local storage',
  },
  {
    id: 3,
    agent: 'VariantCalling',
    title: 'FASTQ → filtered VCF (panel, hg38)',
    payload: '{fastq_r1_uri, fastq_r2_uri}',
    delegation: ['AG1', 'AG2'],
    tool: ['AG2', 'T2'],
    toolName: 'Parabricks / GATK4',
  },
  {
    id: 4,
    agent: 'VariantAnnotation',
    title: 'ClinVar annotation',
    payload: '{vcf_uri}',
    delegation: ['AG2', 'AG3'],
    tool: ['AG3', 'T3'],
    toolName: 'ClinVar',
  },
  {
    id: 5,
    agent: 'VCFAnalysis',
    title: 'Quality control and classification',
    payload: '{annotated_variants_path}',
    delegation: ['AG3', 'AG4'],
    tool: ['AG4', 'T4'],
    toolName: 'Clinical QC + statistics',
  },
  {
    id: 6,
    agent: 'Prediction',
    title: 'Risk level (rules) + verified BioGPT commentary',
    payload: '{panel_analysis}',
    delegation: ['AG4', 'AG5'],
    tool: ['AG5', 'T5'],
    toolName: 'Rules + verified BioGPT',
  },
  {
    id: 7,
    agent: 'ReportGenerator',
    title: 'Reproducible clinical report',
    payload: '{risk_level, rationale}',
    delegation: ['AG5', 'AG6'],
    tool: ['AG6', 'T6'],
    toolName: 'JSON report',
  },
  {
    id: 8,
    agent: 'Orchestrator',
    title: 'Goal reached — end of plan, result returned',
    payload: '{clinical_report, report_uri}',
    delegation: ['AG6', 'MASTER'],
    orchestratorValidation: true,
    validationNode: 'MASTER',
  },
];
