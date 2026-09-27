/** Mermaid diagram — GermlineIQ multi-agent architecture (local server) */
export const ARCHITECTURE_MERMAID = `
flowchart TB
    classDef client fill:#881337,stroke:#f43f5e,color:#fff,stroke-width:2px
    classDef host fill:#4c1d95,stroke:#7c3aed,color:#fff,stroke-width:2px
    classDef agent fill:#0c4a6e,stroke:#0ea5e9,color:#e0f2fe,stroke-width:2px
    classDef pipe fill:#1e293b,stroke:#64748b,color:#f1f5f9,stroke-width:2px

    subgraph HARNESS["LangGraph orchestrator — plan → execute"]
        direction TB
        CLIENT["Interface / API<br/>patient_id + FASTQ or VCF"]:::client
        MASTER["Dynamic planner<br/>tool registry (requires / produces)"]:::host
        subgraph AGENTS["Agents (src/agents)"]
            direction LR
            AG1["DataManager"]:::agent
            AG2["VariantCalling<br/>Parabricks · GATK4"]:::agent
            AG3["VariantAnnotation<br/>ClinVar"]:::agent
            AG4["VCFAnalysis<br/>QC · classification · statistics"]:::agent
            AG5["Prediction<br/>rules + verified BioGPT"]:::agent
            AG6["ReportGenerator"]:::agent
        end
    end

    subgraph PIPELINE["Data — LOCAL_DATA_ROOT (local server)"]
        direction LR
        S1["FASTQ"]:::pipe
        S2["BAM"]:::pipe
        S3["Filtered VCF"]:::pipe
        S4["Annotated variants"]:::pipe
        S5["Panel analysis"]:::pipe
        S6["JSON report"]:::pipe
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
    tag: 'Input',
    title: 'User request',
    description: 'Interface, REST API or assistant: patient_id and FASTQ or VCF paths on the server.',
    dataLabel: 'patient_id · context',
    transport: 'Request',
    highlight: [['CLIENT', 'MASTER']],
  },
  {
    id: 'master',
    tag: 'Planner',
    title: 'Dynamic plan',
    description:
      'Backward chaining from the report: a provided VCF skips alignment, a cached result is reused.',
    dataLabel: 'Tool plan',
    transport: 'Context',
    highlight: [['MASTER', 'AG1']],
  },
  {
    id: 'ag1',
    tag: 'DataManager',
    title: 'FASTQ validation',
    description: 'Checks the FASTQ R1/R2 pair and stores it in patients/<ID>/input.',
    dataLabel: 'FASTQ R1/R2',
    transport: 'FASTQ',
    highlight: [['AG1', 'AG2']],
  },
  {
    id: 'ag2',
    tag: 'VariantCalling',
    title: 'BWA-MEM → BQSR → HaplotypeCaller',
    description:
      'Parabricks (GPU ≥ 16 GB) or GATK4 (CPU), restricted to the panel; normalisation and GATK filters; alignment QC; crash recovery.',
    dataLabel: 'variants.vcf.gz',
    transport: 'VCF',
    highlight: [['AG2', 'AG3']],
  },
  {
    id: 'ag3',
    tag: 'VariantAnnotation',
    title: 'ClinVar annotation',
    description: 'Reads the VCF over the panel regions, local ClinVar annotation (tracked release).',
    dataLabel: 'Annotated variants',
    transport: 'Annotations',
    highlight: [['AG3', 'AG4']],
  },
  {
    id: 'ag4',
    tag: 'VCFAnalysis',
    title: 'Quality control and classification',
    description: 'QUAL, depth, zygosity-aware VAF; confirmed P/LP, to confirm, VUS; statistics and expert checks.',
    dataLabel: 'Panel analysis',
    transport: 'Analysis',
    highlight: [['AG4', 'AG5']],
  },
  {
    id: 'ag5',
    tag: 'Prediction',
    title: 'Risk level',
    description: 'germlineiq-rules-v1.1 rules (HIGH / MODERATE / INDETERMINATE / LOW, coverage of pathogenic sites); BioGPT comments without deciding, every sentence verified.',
    dataLabel: 'Risk + rationale',
    transport: 'Risk',
    highlight: [['AG5', 'AG6']],
  },
  {
    id: 'ag6',
    tag: 'ReportGenerator',
    title: 'Clinical report',
    description: 'Reproducible JSON report (VCF fingerprint, panel and ClinVar versions), archived in the patient folder.',
    dataLabel: 'Clinical report',
    transport: 'Report',
    highlight: [['AG6', 'CLIENT']],
  },
];
