"""
Training data preparation for LLM fine-tuning and machine-learning feature pipelines (legacy, somatic).

Somatic variant metric validation:
- Biological references (ClinGen/CGC/VICC)
- Oncology standards (AMP/ASCO/CAP)
- Quantitative metrics (VAF, CCF, tumour purity)
- Population filtering (gnomAD with a COSMIC whitelist)
- Mutational profiles per subtype (Luminal A/B, HER2, TNBC)

This module is NOT used for the germline risk decision (src.genomics.risk). It only feeds the
optional LoRA training-data agent (train_llm=true) with per-patient feature vectors, in a
structure ready for multi-omic fusion. No predictive performance is claimed for these features.
"""

import json
from pathlib import Path
from typing import List, Dict, Any, Optional, Union, Tuple
from loguru import logger

from src.genomics.panel import get_panel as get_cancer_genes_db
from src.genomics.variant import Variant

# ---------------------------------------------------------------------------
# Constants following ClinGen/CGC/VICC and AMP/ASCO/CAP standards
# ---------------------------------------------------------------------------

# VAF thresholds per GENIE/AMP guidelines (Section 3.1)
MIN_VAF_SOMATIC_LOD = 0.05  # Limit of detection (LOD) for solid tissue
MIN_VAF_LIQUID_BIOPSY = 0.001  # 0.1% for liquid biopsies
MIN_DEPTH_RELIABLE = 20  # DP < 20x = not statistically significant
MIN_DEPTH_HIGH_CONFIDENCE = 500  # For VAF < 0.05

# gnomAD thresholds for germline filtering (Section 4.1)
GNOMAD_AF_GERMLINE_THRESHOLD = 0.001  # 0.1% = standard threshold
GNOMAD_AF_VERY_RARE = 0.0001  # 0.01% = very rare

# CCF thresholds for clonal classification (Section 3.2.2)
CCF_CLONAL_THRESHOLD = 0.90  # CCF ≥ 0.90 = clonal (truncal)
CCF_SUBCLONAL_MAX = 0.90  # CCF < 0.90 = subclonal (branched)

# ClinGen/CGC/VICC scores (Section 2.1)
CLINGEN_ONCOGENIC_THRESHOLD = 10  # Score ≥ 10 = Oncogenic
CLINGEN_LIKELY_ONCOGENIC_MIN = 6  # Score 6-9 = Likely oncogenic
CLINGEN_LIKELY_ONCOGENIC_MAX = 9
CLINGEN_VUS_MAX = 5  # Score 0-5 = VUS
CLINGEN_LIKELY_BENIGN_MIN = -6  # Score -1 to -6 = Likely benign
CLINGEN_BENIGN_THRESHOLD = -7  # Score ≤ -7 = Benign

# ClinGen evidence points (Section 2.2)
EVIDENCE_VERY_STRONG = 8  # O_VS (e.g. null variant in a TSG)
EVIDENCE_STRONG = 4  # O_S (e.g. mutational hotspot)
EVIDENCE_MODERATE = 2  # O_M (e.g. critical functional domain)
EVIDENCE_SUPPORTING = 1  # O_P (e.g. in silico prediction)

# Canonical PIK3CA hotspots (Section 5.2.1)
PIK3CA_HOTSPOTS = {
    "E542K": {"gene": "PIK3CA", "aa_change": "E542K", "domain": "helical"},
    "E545K": {"gene": "PIK3CA", "aa_change": "E545K", "domain": "helical"},
    "H1047R": {"gene": "PIK3CA", "aa_change": "H1047R", "domain": "kinase"},
    "H1047L": {"gene": "PIK3CA", "aa_change": "H1047L", "domain": "kinase"},
}

# Tumour suppressor genes (for PVS1/O_VS)
TUMOR_SUPPRESSOR_GENES = frozenset({
    "BRCA1", "BRCA2", "TP53", "PTEN", "RB1", "APC", "VHL", "NF1", "NF2"
})

# Literature value quoted for multi-omic + VCF models; not reproduced or validated in this project
SOTA_TARGET_AUC = 0.93

# Breast cancer subtype mutational profiles (Section 5.1)
BREAST_CANCER_SUBTYPE_PROFILES = {
    "Luminal_A": {
        "PIK3CA_freq": 0.45,
        "TP53_freq": 0.12,
        "MAP3K1_freq": 0.14,
        "GATA3_freq": 0.14,
        "key_genes": ["PIK3CA", "MAP3K1", "GATA3"],
    },
    "Luminal_B": {
        "PIK3CA_freq": 0.45,  # Approximatif
        "TP53_freq": 0.29,
        "key_genes": ["PIK3CA", "TP53"],
    },
    "HER2_Enriched": {
        "TP53_freq": 0.72,
        "PIK3CA_freq": 0.39,
        "key_genes": ["ERBB2", "TP53", "PIK3CA"],
    },
    "TNBC": {
        "TP53_freq": 0.82,  # 80-84%
        "PIK3CA_freq": 0.09,
        "BRCA1_freq": 0.20,
        "key_genes": ["TP53", "BRCA1", "BRCA2"],
    },
}


class TrainingDataPreparationError(Exception):
    """Error in training data preparation."""

    pass


class TrainingDataPreparation:
    """
    Prepare training data for LLM fine-tuning.
    
    Implements validation standards for real metrics:
    - CCF (cancer cell fraction) computation with tumour purity
    - ClinGen/CGC/VICC oncogenicity score (points)
    - Germline/somatic distinction
    - gnomAD filtering with a COSMIC whitelist
    - Sequencing depth validation
    - Mutational profiles per subtype
    """

    def __init__(self, tumor_purity: Optional[float] = None):
        """
        Initialize training data preparation.
        
        Args:
            tumor_purity: Tumour purity (0-1) for CCF computation. If None, raw VAF is used.
        """
        self.logger = logger
        self.tumor_purity = tumor_purity
        try:
            self.cancer_genes_db = get_cancer_genes_db()
        except Exception:
            self.cancer_genes_db = None

    def prepare_from_metrics_json(
        self,
        metrics_source: Union[str, Path, Dict[str, Any]],
        analysis_result: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Prepare a training example from the VCF metrics JSON (GATK/VCF pipeline output).

        This JSON is produced by src.genomics.analysis.to_vcf_metrics and passed by the orchestrator
        to the training-data agent.

        Args:
            metrics_source: Path to the vcf_metrics.json file or a dict (metadata + summary + variants)
            analysis_result: Optional analysis result for the assistant message

        Returns:
            Training example (messages + metadata)
        """
        if isinstance(metrics_source, (str, Path)):
            with open(metrics_source, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = metrics_source
        metadata = data.get("metadata", {})
        summary = data.get("summary", {})
        variants = data.get("variants", [])
        patient_id = metadata.get("patient_id", "unknown")
        coverage = metadata.get("coverage", 30.0)
        return self.prepare_from_vcf_analysis(
            patient_id=patient_id,
            variants=variants,
            coverage=float(coverage),
            analysis_result=analysis_result or {
                "summary": summary,
                "breast_cancer_detected": metadata.get("breast_cancer_detected", False),
            },
        )

    def prepare_from_vcf_analysis(
        self,
        patient_id: str,
        variants: Union[List[Dict], List[Variant]],
        coverage: float,
        analysis_result: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Prepare training example from VCF analysis.

        Args:
            patient_id: Patient identifier
            variants: List of variant dictionaries or Variant objects
            coverage: Sequencing coverage
            analysis_result: Optional analysis result dictionary

        Returns:
            Training example dictionary in chat format
        """
        from src.llm.prompt_templates import GenomicPromptTemplates

        # Convert Variant objects to enriched dictionaries if needed
        if variants and isinstance(variants[0], Variant):
            variant_dicts = [self._variant_to_dict(v) for v in variants]
        else:
            variant_dicts = variants

        # Calculate patient-level metrics
        patient_metrics = self._calculate_patient_metrics(variant_dicts, coverage)

        # Format user prompt with enriched variant data
        user_prompt = GenomicPromptTemplates.format_user_prompt(
            patient_id=patient_id,
            variants=variant_dicts,
            coverage=coverage,
        )

        # Create system message
        system_message = {
            "role": "system",
            "content": GenomicPromptTemplates.SYSTEM_PROMPT,
        }

        # Create user message
        user_message = {
            "role": "user",
            "content": user_prompt,
        }

        # Create assistant message (from analysis result or default)
        if analysis_result:
            assistant_content = self._format_analysis_result(analysis_result)
        else:
            assistant_content = self._generate_default_analysis(patient_id, variant_dicts)

        assistant_message = {
            "role": "assistant",
            "content": assistant_content,
        }

        # Create training example with enriched metadata
        training_example = {
            "messages": [system_message, user_message, assistant_message],
            "patient_id": patient_id,
            "metadata": patient_metrics,
        }

        return training_example

    def build_patient_feature_vector(
        self,
        variants: List[Dict],
        coverage: float,
        tumor_purity: Optional[float] = None,
        include_gene_counts: bool = True,
        include_distributions: bool = True,
    ) -> Dict[str, Union[int, float, List[float]]]:
        """
        Builds a per-patient numeric feature vector for machine-learning models.

        Designed for multi-omic integration: VCF-derived features can be concatenated
        with transcriptomics, methylation, etc.

        Args:
            variants: List of already enriched variants (with vaf, ccf, oncogenicity_score, etc.)
            coverage: Mean sequencing depth
            tumor_purity: Tumour purity if available (else NaN)
            include_gene_counts: Include counts per key gene (one-hot / counts)
            include_distributions: Inclure stats de distribution (VAF, CCF)

        Returns:
            Dict of numeric features (ready to concatenate with other omics)
        """
        somatic = [v for v in variants if not v.get("is_filtered_germinal", False)]
        n = len(somatic)

        # --- Aggregated scalar features ---
        feats = {
            "vcf_variant_count": n,
            "vcf_coverage": coverage,
            "vcf_tumor_purity": tumor_purity if tumor_purity is not None else float("nan"),
            "vcf_oncogenic_count": sum(
                1 for v in somatic
                if v.get("oncogenicity_class") in ("Oncogenic", "Likely_Oncogenic")
            ),
            "vcf_clonal_driver_count": sum(
                1 for v in somatic
                if v.get("clonality") == "Clonal"
                and v.get("oncogenicity_class") in ("Oncogenic", "Likely_Oncogenic")
            ),
            "vcf_pathogenic_count": sum(1 for v in somatic if v.get("is_pathogenic")),
            "vcf_hotspot_count": sum(1 for v in somatic if v.get("hotspot")),
            "vcf_rare_count": sum(1 for v in somatic if v.get("is_rare")),
            "vcf_high_confidence_count": sum(
                1 for v in somatic
                if v.get("is_reliable_depth") and v.get("confidence_level") == "High"
            ),
        }

        # --- Aggregated scores ---
        onc_scores = [v.get("oncogenicity_score") for v in somatic if v.get("oncogenicity_score") is not None]
        feats["vcf_oncogenicity_score_sum"] = sum(onc_scores) if onc_scores else 0.0
        feats["vcf_oncogenicity_score_max"] = max(onc_scores) if onc_scores else 0.0
        feats["vcf_oncogenicity_score_mean"] = (sum(onc_scores) / len(onc_scores)) if onc_scores else 0.0

        vafs = [v.get("vaf") for v in somatic if v.get("vaf") is not None]
        if include_distributions and vafs:
            feats["vcf_vaf_mean"] = sum(vafs) / len(vafs)
            feats["vcf_vaf_max"] = max(vafs)
            feats["vcf_vaf_std"] = (sum((x - feats["vcf_vaf_mean"]) ** 2 for x in vafs) / len(vafs)) ** 0.5
        else:
            feats["vcf_vaf_mean"] = feats["vcf_vaf_max"] = feats["vcf_vaf_std"] = float("nan")

        ccf_list = [v.get("ccf") for v in somatic if v.get("ccf") is not None]
        if include_distributions and ccf_list:
            feats["vcf_ccf_mean"] = sum(ccf_list) / len(ccf_list)
            feats["vcf_ccf_max"] = max(ccf_list)
            feats["vcf_ccf_std"] = (sum((x - feats["vcf_ccf_mean"]) ** 2 for x in ccf_list) / len(ccf_list)) ** 0.5
        else:
            feats["vcf_ccf_mean"] = feats["vcf_ccf_max"] = feats["vcf_ccf_std"] = float("nan")

        # --- Counts per key gene (subtypes) ---
        if include_gene_counts:
            key_genes = set()
            for profile in BREAST_CANCER_SUBTYPE_PROFILES.values():
                key_genes.update(profile.get("key_genes", []))
            for g in key_genes:
                feats[f"vcf_gene_{g}_count"] = sum(1 for v in somatic if (v.get("gene") or "").upper() == g)
            feats["vcf_key_gene_mutation_count"] = sum(
                feats.get(f"vcf_gene_{g}_count", 0) for g in key_genes
            )

        # --- Derived binary indicators (interactions) ---
        feats["vcf_has_clonal_driver"] = 1.0 if feats["vcf_clonal_driver_count"] > 0 else 0.0
        feats["vcf_has_oncogenic"] = 1.0 if feats["vcf_oncogenic_count"] > 0 else 0.0
        feats["vcf_high_burden"] = 1.0 if n >= 5 else 0.0  # arbitrary threshold
        feats["vcf_reliable_burden_ratio"] = (
            feats["vcf_high_confidence_count"] / n if n > 0 else 0.0
        )

        return feats

    def prepare_sota_training_example(
        self,
        patient_id: str,
        variants: Union[List[Dict], List[Any]],
        coverage: float,
        label: Optional[Union[bool, int, float]] = None,
        analysis_result: Optional[Dict] = None,
        tumor_purity: Optional[float] = None,
        multi_omic_placeholder: Optional[Dict[str, Union[int, float, List[float]]]] = None,
    ) -> Dict[str, Any]:
        """
        Prepares a training example combining chat messages and a feature vector.

        Combine :
        - chat messages (for LLM fine-tuning)
        - metadata patient
        - VCF feature vector for ML / multi-omic fusion
        - optional slot for other omics (transcriptomics, methylation)

        Intended for models integrating multi-omic data and VCF features.

        Args:
            patient_id: Identifiant patient
            variants: List of variants (Variant or dict)
            coverage: Profondeur
            label: Ground-truth label (cancer yes/no or score) for AUC evaluation
            analysis_result: Analysis result for the assistant message
            tumor_purity: Tumour purity for CCF
            multi_omic_placeholder: Dict of other-omic features to merge (prefixed keys)

        Returns:
            Dict with messages, metadata, feature_vector_vcf, optional feature_vector_multi_omic, label
        """
        # Enrich variants if they are Variant objects
        if variants and Variant is not None and isinstance(variants[0], Variant):
            variant_dicts = [self._variant_to_dict(v) for v in variants]
        else:
            variant_dicts = list(variants) if variants else []

        # Use the given purity or the instance one
        purity = tumor_purity if tumor_purity is not None else self.tumor_purity

        # Exemple chat (comme avant)
        base_example = self.prepare_from_vcf_analysis(
            patient_id=patient_id,
            variants=variant_dicts,
            coverage=coverage,
            analysis_result=analysis_result,
        )

        # VCF feature vector
        feature_vector_vcf = self.build_patient_feature_vector(
            variant_dicts, coverage, tumor_purity=purity,
            include_gene_counts=True,
            include_distributions=True,
        )

        out = {
            "patient_id": patient_id,
            "messages": base_example["messages"],
            "metadata": base_example["metadata"],
            "feature_vector_vcf": feature_vector_vcf,
            "sota_target_auc": SOTA_TARGET_AUC,
        }
        if label is not None:
            out["label"] = label
        if multi_omic_placeholder:
            out["feature_vector_other_omics"] = multi_omic_placeholder
            # Recommended concatenation order: VCF then other omics
            out["feature_vector_combined_keys"] = (
                list(feature_vector_vcf.keys()) + list(multi_omic_placeholder.keys())
            )
        return out

    def get_sota_feature_names(self) -> List[str]:
        """Returns the VCF feature names for reproducibility and ML pipelines."""
        key_genes = set()
        for profile in BREAST_CANCER_SUBTYPE_PROFILES.values():
            key_genes.update(profile.get("key_genes", []))
        base = [
            "vcf_variant_count", "vcf_coverage", "vcf_tumor_purity",
            "vcf_oncogenic_count", "vcf_clonal_driver_count", "vcf_pathogenic_count",
            "vcf_hotspot_count", "vcf_rare_count", "vcf_high_confidence_count",
            "vcf_oncogenicity_score_sum", "vcf_oncogenicity_score_max", "vcf_oncogenicity_score_mean",
            "vcf_vaf_mean", "vcf_vaf_max", "vcf_vaf_std",
            "vcf_ccf_mean", "vcf_ccf_max", "vcf_ccf_std",
            "vcf_has_clonal_driver", "vcf_has_oncogenic", "vcf_high_burden", "vcf_reliable_burden_ratio",
            "vcf_key_gene_mutation_count",
        ]
        return base + [f"vcf_gene_{g}_count" for g in sorted(key_genes)]

    def _variant_to_dict(self, variant: Variant) -> Dict[str, Any]:
        """
        Convert Variant object to enriched dictionary with validated metrics.
        
        Implements the validation standards:
        - CCF computation when tumour purity is available
        - ClinGen oncogenicity score (points)
        - Distinction germinal/somatique
        - Validation profondeur (DP ≥ 20x)
        - gnomAD filtering with a COSMIC whitelist

        Args:
            variant: Variant object

        Returns:
            Dictionary with all validated variant metrics
        """
        # Determine variant type
        variant_type = self._determine_variant_type(variant.ref, variant.alt)
        
        # Get allele frequency (AF) - prefer gnomAD, fallback to INFO AF
        af = variant.gnomad_af
        if af is None and variant.info:
            if "AF" in variant.info:
                af_val = variant.info["AF"]
                if isinstance(af_val, (list, tuple)) and len(af_val) > 0:
                    af = float(af_val[0])
                elif isinstance(af_val, (int, float)):
                    af = float(af_val)
        
        # Get VAF (variant allele frequency)
        vaf = variant.vaf
        
        # Get depth
        dp = variant.depth
        
        # Validation profondeur (Section 6.1)
        is_reliable_depth = dp is not None and dp >= MIN_DEPTH_RELIABLE
        confidence_level = self._assess_vaf_confidence(vaf, dp)
        
        # Distinction germinal/somatique (Section 3.1.2)
        is_likely_germinal = self._is_likely_germinal(vaf, af)
        
        # gnomAD filtering with a COSMIC whitelist (Section 4.1)
        is_filtered_germinal = self._should_filter_as_germinal(vaf, af, variant)
        
        # CCF computation when tumour purity is available (Section 3.2.1)
        ccf = None
        clonality = None
        if vaf is not None and self.tumor_purity is not None:
            ccf, clonality = self._calculate_ccf(
                vaf=vaf,
                tumor_purity=self.tumor_purity,
                copy_number_tumor=2,  # Diploid by default, may come from CNV data
                copy_number_normal=2,
                mutation_multiplicity=1,  # Heterozygous by default
            )
        
        # ClinGen/CGC/VICC oncogenicity score (Section 2)
        oncogenicity_score, oncogenicity_class = self._calculate_clingen_oncogenicity_score(variant)
        
        # Check if hotspot
        is_hotspot = self._is_hotspot(variant)
        
        # Check if in cancer gene
        is_cancer_gene = False
        if self.cancer_genes_db and variant.gene:
            is_cancer_gene = self.cancer_genes_db.is_cancer_gene(variant.gene)
        
        # Determine if rare (AF < 0.01 or not in population databases)
        is_rare = af is None or af < GNOMAD_AF_GERMLINE_THRESHOLD
        
        variant_dict = {
            "chromosome": variant.chromosome,
            "position": variant.position,
            "ref": variant.ref,
            "alt": variant.alt,
            "gene": variant.gene or "Unknown",
            "variant_type": variant_type,
            "consequence": variant.consequence or "Unknown",
            "vaf": round(vaf, 4) if vaf is not None else None,
            "af": round(af, 6) if af is not None else None,
            "dp": dp,
            "quality": variant.quality,
            "clinvar": variant.clinvar or "Not reported",
            "hotspot": is_hotspot,
            "is_pathogenic": variant.is_pathogenic,
            "is_rare": is_rare,
            "is_cancer_gene": is_cancer_gene,
            # Additional validated metrics
            "ccf": round(ccf, 3) if ccf is not None else None,
            "clonality": clonality,
            "is_likely_germinal": is_likely_germinal,
            "is_filtered_germinal": is_filtered_germinal,
            "is_reliable_depth": is_reliable_depth,
            "confidence_level": confidence_level,
            "oncogenicity_score": oncogenicity_score,
            "oncogenicity_class": oncogenicity_class,
        }
        
        return variant_dict

    def _determine_variant_type(self, ref: str, alt: str) -> str:
        """Determine variant type (SNV, INDEL, etc.)."""
        if len(ref) == 1 and len(alt) == 1:
            return "SNV"
        elif len(ref) > len(alt):
            return "Deletion"
        elif len(alt) > len(ref):
            return "Insertion"
        else:
            return "Complex"

    def _calculate_clingen_oncogenicity_score(self, variant: Variant) -> Tuple[int, str]:
        """
        Computes the oncogenicity score with the ClinGen/CGC/VICC system (Section 2).
        
        Returns:
            Tuple (total_score, classification) where classification is:
            - "Oncogenic" (≥10)
            - "Likely_Oncogenic" (6-9)
            - "VUS" (0-5)
            - "Likely_Benign" (-1 to -6)
            - "Benign" (≤-7)
        """
        score = 0
        
        gene = (variant.gene or "").upper()
        consequence = (variant.consequence or "").lower()
        is_hotspot = self._is_hotspot(variant)
        
        # Very strong evidence (O_VS = +8): null variant in a TSG (Section 2.2)
        if gene in TUMOR_SUPPRESSOR_GENES:
            if any(term in consequence for term in ["frameshift", "stop", "nonsense", "splice"]):
                score += EVIDENCE_VERY_STRONG
                self.logger.debug(f"{gene}: Null variant in TSG -> +{EVIDENCE_VERY_STRONG} (O_VS)")
        
        # Preuve Forte (O_S = +4) : Hotspot mutationnel (Section 2.2, 5.2.1)
        if is_hotspot:
            # Check whether it is a canonical PIK3CA hotspot
            if gene == "PIK3CA" and variant.info:
                # Check COSMIC annotation or known position
                if "COSMIC" in variant.info or any(
                    hotspot_info["aa_change"] in str(variant.info.get("AA", ""))
                    for hotspot_info in PIK3CA_HOTSPOTS.values()
                ):
                    score += EVIDENCE_STRONG
                    self.logger.debug(f"{gene}: Canonical hotspot -> +{EVIDENCE_STRONG} (O_S)")
            elif is_hotspot:
                score += EVIDENCE_STRONG
                self.logger.debug(f"{gene}: Hotspot detected -> +{EVIDENCE_STRONG} (O_S)")
        
        # Moderate evidence (O_M = +2): critical functional domain (Section 2.2)
        if variant.consequence:
            if "missense" in consequence:
                # Check whether in a critical domain (e.g. kinase, DNA-binding)
                if gene == "PIK3CA" and "kinase" in str(variant.info.get("Domain", "")).lower():
                    score += EVIDENCE_MODERATE
                elif gene == "TP53" and "dna-binding" in str(variant.info.get("Domain", "")).lower():
                    score += EVIDENCE_MODERATE
                else:
                    # Missense in a cancer gene = moderate by default
                    if self.cancer_genes_db and gene and self.cancer_genes_db.is_cancer_gene(gene):
                        score += EVIDENCE_MODERATE
        
        # Supporting evidence (O_P = +1): in silico predictions or low recurrence
        if variant.gnomad_af is not None and variant.gnomad_af < GNOMAD_AF_VERY_RARE:
            score += EVIDENCE_SUPPORTING
        elif variant.gnomad_af is None:  # Absent from gnomAD
            score += EVIDENCE_SUPPORTING
        
        # Penalties for frequent variants (benign)
        if variant.gnomad_af is not None and variant.gnomad_af > GNOMAD_AF_GERMLINE_THRESHOLD:
            # If frequent AND not in the COSMIC whitelist
            if not is_hotspot:
                score -= 2  # Penalty for a common polymorphism
        
        # Classification selon seuils (Section 2.1)
        if score >= CLINGEN_ONCOGENIC_THRESHOLD:
            classification = "Oncogenic"
        elif CLINGEN_LIKELY_ONCOGENIC_MIN <= score <= CLINGEN_LIKELY_ONCOGENIC_MAX:
            classification = "Likely_Oncogenic"
        elif CLINGEN_LIKELY_BENIGN_MIN <= score <= 0:
            classification = "Likely_Benign"
        elif score <= CLINGEN_BENIGN_THRESHOLD:
            classification = "Benign"
        else:
            classification = "VUS"
        
        return score, classification
    
    def _calculate_ccf(
        self,
        vaf: float,
        tumor_purity: float,
        copy_number_tumor: int = 2,
        copy_number_normal: int = 2,
        mutation_multiplicity: int = 1,
    ) -> Tuple[Optional[float], Optional[str]]:
        """
        Computes the cancer cell fraction (CCF) as in Section 3.2.1.
        
        Formule : CCF = (VAF × [p × CN_t + (1-p) × CN_n]) / (p × CN_mut)
        
        Args:
            vaf: Variant Allele Frequency (0-1)
            tumor_purity: Tumour purity p (0-1)
            copy_number_tumor: CN_t (total copy number in tumour cells)
            copy_number_normal: CN_n (usually 2)
            mutation_multiplicity: CN_mut (mutation multiplicity)
        
        Returns:
            Tuple (ccf, clonality) where clonality is "Clonal" or "Subclonal"
        """
        if vaf is None or tumor_purity is None or tumor_purity <= 0:
            return None, None
        
        # CCF from the reference formula
        numerator = vaf * (tumor_purity * copy_number_tumor + (1 - tumor_purity) * copy_number_normal)
        denominator = tumor_purity * mutation_multiplicity
        
        if denominator == 0:
            return None, None
        
        ccf = numerator / denominator
        
        # Cap CCF at 1.0 (cannot exceed 100% of cancer cells)
        ccf = min(1.0, max(0.0, ccf))
        
        # Classification clonale (Section 3.2.2)
        if ccf >= CCF_CLONAL_THRESHOLD:
            clonality = "Clonal"
        else:
            clonality = "Subclonal"
        
        return ccf, clonality
    
    def _is_likely_germinal(self, vaf: Optional[float], af: Optional[float]) -> bool:
        """
        Determines whether a variant is probably germline (Section 3.1.2).
        
        Criteria:
        - VAF > 0.45 (heterozygous) or ~1.0 (homozygous)
        - AND population frequency > 0.01 (1%)
        """
        if vaf is None:
            return False
        
        # VAF close to 50% (heterozygous) or 100% (homozygous)
        is_het_germinal = 0.45 <= vaf <= 0.55
        is_hom_germinal = vaf >= 0.95
        
        # High population frequency
        is_common_population = af is not None and af > 0.01
        
        return (is_het_germinal or is_hom_germinal) and is_common_population
    
    def _should_filter_as_germinal(self, vaf: Optional[float], af: Optional[float], variant: Variant) -> bool:
        """
        Determines whether a variant must be filtered as germline (Section 4.1).
        
        Critical validation rule: if popAF > 0.001 BUT present in COSMIC,
        do NOT filter (whitelist).
        """
        if af is None or af < GNOMAD_AF_GERMLINE_THRESHOLD:
            return False  # Too rare to be a common polymorphism
        
        # COSMIC whitelist: never filter hotspots even if frequent
        is_cosmic_hotspot = self._is_hotspot(variant)
        if is_cosmic_hotspot:
            return False  # Rescued by the whitelist
        
        # Filter if frequent AND not in the whitelist
        return True
    
    def _assess_vaf_confidence(self, vaf: Optional[float], dp: Optional[int]) -> str:
        """
        Assesses the confidence level of a VAF call (Section 3.1.1).
        
        Returns:
            "High", "Medium", "Low", or "Unreliable"
        """
        if vaf is None or dp is None:
            return "Unreliable"
        
        # Validation selon Section 3.1.1
        if vaf < MIN_VAF_SOMATIC_LOD:
            if dp < MIN_DEPTH_HIGH_CONFIDENCE:
                return "Low"  # Low VAF AND insufficient depth
            else:
                return "Medium"  # VAF faible mais profondeur OK
        
        if dp < MIN_DEPTH_RELIABLE:
            return "Low"  # Profondeur < 20x = non significatif
        
        return "High"  # VAF ≥ 5% and DP ≥ 20x

    def _is_hotspot(self, variant: Variant) -> bool:
        """
        Checks whether a variant lies in a known hotspot (Section 5.2.1).
        
        Checks:
        - COSMIC/HOTSPOT annotations in INFO
        - Hotspots canoniques PIK3CA (E542K, E545K, H1047R, H1047L)
        """
        if not variant.info:
            return False
        
        # Annotation explicite
        if "HOTSPOT" in variant.info or "COSMIC" in variant.info:
            return True
        
        # Check by field name
        if any("hotspot" in str(k).lower() for k in variant.info.keys()):
            return True
        
        # Hotspots canoniques PIK3CA
        if variant.gene and variant.gene.upper() == "PIK3CA":
            # Check the amino-acid change
            aa_change = variant.info.get("AA", "")
            if isinstance(aa_change, str):
                for hotspot_name in PIK3CA_HOTSPOTS.keys():
                    if hotspot_name in aa_change:
                        return True
        
        return False
    
    def _validate_subtype_profile(self, variants: List[Dict], subtype: Optional[str] = None) -> Dict[str, Any]:
        """
        Validates the mutational profile against the breast cancer subtype (Section 5.1).
        
        Args:
            variants: Liste de variants enrichis
            subtype: Sous-type attendu ("Luminal_A", "Luminal_B", "HER2_Enriched", "TNBC")
        
        Returns:
            Dict with validation and quality alerts
        """
        if not subtype or subtype not in BREAST_CANCER_SUBTYPE_PROFILES:
            return {"validated": False, "alerts": []}
        
        profile = BREAST_CANCER_SUBTYPE_PROFILES[subtype]
        alerts = []
        
        # Count mutations per key gene
        gene_counts = {}
        for variant in variants:
            gene = variant.get("gene", "").upper()
            if gene:
                gene_counts[gene] = gene_counts.get(gene, 0) + 1
        
        # Subtype-specific checks
        if subtype == "TNBC":
            # TP53 is expected in ~80-84% of cases
            has_tp53 = "TP53" in gene_counts
            if not has_tp53:
                alerts.append(
                    "QUALITY ALERT: TNBC without a detected TP53 mutation. "
                    "Biologically unlikely (although possible). Manual review recommended."
                )
        
        elif subtype == "Luminal_A":
            # PIK3CA is expected to be frequent (~45%)
            has_pik3ca = "PIK3CA" in gene_counts
            if not has_pik3ca:
                alerts.append(
                    "INFO: Luminal A without a detected PIK3CA mutation. "
                    "May be valid but less frequent."
                )
        
        return {
            "validated": len(alerts) == 0,
            "alerts": alerts,
            "gene_counts": gene_counts,
        }

    def _calculate_patient_metrics(
        self, variants: List[Dict], coverage: float
    ) -> Dict[str, Any]:
        """
        Computes aggregated patient-level metrics (Section 7).

        Args:
            variants: List of variant dictionaries (enriched)
            coverage: Sequencing coverage

        Returns:
            Dictionary with validated patient-level metrics
        """
        if not variants:
            return {
                "coverage": coverage,
                "variant_count": 0,
                "high_impact_count": 0,
                "rare_variant_count": 0,
                "cancer_gene_count": 0,
                "pathogenic_count": 0,
                "oncogenic_count": 0,
                "clonal_driver_count": 0,
                "reliable_variant_count": 0,
            }
        
        # Filter out germline variants for somatic metrics
        somatic_variants = [v for v in variants if not v.get("is_filtered_germinal", False)]
        
        # Standard metrics
        rare_variant_count = sum(1 for v in somatic_variants if v.get("is_rare", False))
        cancer_gene_count = sum(1 for v in somatic_variants if v.get("is_cancer_gene", False))
        pathogenic_count = sum(1 for v in somatic_variants if v.get("is_pathogenic", False))
        
        # Additional validated metrics
        oncogenic_count = sum(
            1 for v in somatic_variants
            if v.get("oncogenicity_class") in ("Oncogenic", "Likely_Oncogenic")
        )
        clonal_driver_count = sum(
            1 for v in somatic_variants
            if v.get("clonality") == "Clonal" and v.get("oncogenicity_class") in ("Oncogenic", "Likely_Oncogenic")
        )
        reliable_variant_count = sum(
            1 for v in somatic_variants
            if v.get("is_reliable_depth", False) and v.get("confidence_level") == "High"
        )
        
        return {
            "coverage": coverage,
            "variant_count": len(somatic_variants),
            "rare_variant_count": rare_variant_count,
            "cancer_gene_count": cancer_gene_count,
            "pathogenic_count": pathogenic_count,
            "oncogenic_count": oncogenic_count,
            "clonal_driver_count": clonal_driver_count,
            "reliable_variant_count": reliable_variant_count,
            # Quality validation
            "has_reliable_depth": coverage >= MIN_DEPTH_RELIABLE,
            "germinal_filtered_count": len(variants) - len(somatic_variants),
        }

    def _format_analysis_result(self, analysis_result: Dict) -> str:
        """
        Format analysis result as assistant message.

        Args:
            analysis_result: Analysis result dictionary

        Returns:
            Formatted analysis text
        """
        parts = []

        if "summary" in analysis_result:
            parts.append(f"## Summary\n{analysis_result['summary']}")

        if "findings" in analysis_result:
            parts.append(f"## Key Findings\n{analysis_result['findings']}")

        if "risk_assessment" in analysis_result:
            parts.append(f"## Risk Assessment\n{analysis_result['risk_assessment']}")

        if "recommendations" in analysis_result:
            parts.append(f"## Recommendations\n{analysis_result['recommendations']}")

        if "urgent_variants" in analysis_result:
            urgent = analysis_result["urgent_variants"]
            if urgent:
                parts.append(f"## Variants Requiring Immediate Attention\n{urgent}")

        return "\n\n".join(parts) if parts else "No significant findings detected."

    def _generate_default_analysis(
        self, patient_id: str, variants: List[Dict]
    ) -> str:
        """
        Generate default analysis text.

        Args:
            patient_id: Patient identifier
            variants: List of variants

        Returns:
            Default analysis text
        """
        if not variants:
            return (
                "## Summary\n"
                "No pathogenic variants detected in this sample.\n\n"
                "## Risk Assessment\n"
                "Based on the current analysis, no elevated cancer risk is indicated.\n\n"
                "## Recommendations\n"
                "Continue with standard screening protocols."
            )

        variant_count = len(variants)
        return (
            f"## Summary\n"
            f"Analysis of patient {patient_id} identified {variant_count} variant(s) "
            f"requiring clinical review.\n\n"
            f"## Key Findings\n"
            f"Multiple variants detected that may have clinical significance.\n\n"
            f"## Risk Assessment\n"
            f"Further evaluation recommended to assess cancer risk.\n\n"
            f"## Recommendations\n"
            f"1. Review variant annotations and clinical significance\n"
            f"2. Consider additional testing if indicated\n"
            f"3. Consult with genetics team for interpretation"
        )

    def load_training_data(self, file_path: Path) -> List[Dict[str, Any]]:
        """
        Load training data from JSONL file.

        Args:
            file_path: Path to JSONL file

        Returns:
            List of training examples
        """
        if not file_path.exists():
            self.logger.warning(f"Training data file not found: {file_path}")
            return []

        examples = []
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        examples.append(json.loads(line))

            self.logger.info(f"Loaded {len(examples)} training examples from {file_path}")
        except Exception as e:
            error_msg = f"Error loading training data from {file_path}: {str(e)}"
            self.logger.error(error_msg)
            raise TrainingDataPreparationError(error_msg) from e

        return examples

    def save_training_data(
        self, examples: List[Dict[str, Any]], file_path: Path
    ) -> None:
        """
        Save training data to JSONL file.

        Args:
            examples: List of training examples
            file_path: Path to save JSONL file
        """
        file_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                for example in examples:
                    f.write(json.dumps(example, ensure_ascii=False) + "\n")

            self.logger.info(f"Saved {len(examples)} training examples to {file_path}")
        except Exception as e:
            error_msg = f"Error saving training data to {file_path}: {str(e)}"
            self.logger.error(error_msg)
            raise TrainingDataPreparationError(error_msg) from e

