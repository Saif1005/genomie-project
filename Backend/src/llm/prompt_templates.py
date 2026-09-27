"""Prompt templates for genomic cancer detection LLM."""

from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class PromptTemplate:
    """Template for LLM prompts."""

    system: str
    user: str


class GenomicPromptTemplates:
    """Prompt templates for genomic cancer detection."""

    SYSTEM_PROMPT = """You are a clinical genomic oncologist assistant. Output MUST be prescriptive, not conversational.
Use exactly these labeled lines (no extra prose before or after):

RISK_LEVEL: HIGH | MODERATE | LOW
DIAGNOSTIC_CONCLUSION: Hereditary breast cancer risk: HIGH | MODERATE | LOW
CLINICAL_SUMMARY: One formal medical paragraph in English explaining the specific mutation impact (gene, position, DNA repair, inheritance, breast/ovarian risk).
Do not use bullet points. Do not address the reader as "you". Base conclusions only on provided variants."""

    PRESCRIPTIVE_SUFFIX = """
Reply ONLY with the three labelled lines:
RISK_LEVEL:
DIAGNOSTIC_CONCLUSION:
CLINICAL_SUMMARY:"""

    @staticmethod
    def format_user_prompt(
        patient_id: str,
        variants: List[Dict],
        coverage: float,
        tmb: Optional[float] = None,
        quality_metrics: Optional[Dict] = None,
    ) -> str:
        """
        Format user prompt for genomic analysis.

        Args:
            patient_id: Patient identifier
            variants: List of variant dictionaries with keys like 'gene', 'variant', 'impact', etc.
            coverage: Sequencing coverage depth
            tmb: Tumor mutational burden (optional)
            quality_metrics: Quality metrics dictionary (optional)

        Returns:
            Formatted prompt string
        """
        prompt_parts = [
            f"Patient ID: {patient_id}",
            f"Sequencing Coverage: {coverage:.1f}x",
        ]

        if tmb is not None:
            prompt_parts.append(f"Tumor Mutational Burden (TMB): {tmb:.2f} mutations/Mb")

        if quality_metrics:
            qc_info = ", ".join([f"{k}: {v}" for k, v in quality_metrics.items()])
            prompt_parts.append(f"Quality Metrics: {qc_info}")

        prompt_parts.append("\nDetected Variants:")
        prompt_parts.append("-" * 50)

        if not variants:
            prompt_parts.append("No pathogenic variants detected.")
        else:
            for i, variant in enumerate(variants, 1):
                variant_info = []
                
                # Gene and variant identifier
                gene = variant.get("gene", "Unknown")
                chrom = variant.get("chromosome", "")
                pos = variant.get("position", "")
                ref = variant.get("ref", "")
                alt = variant.get("alt", "")
                
                if gene and gene != "Unknown":
                    variant_info.append(f"Gene: {gene}")
                if chrom and pos:
                    variant_info.append(f"Location: {chrom}:{pos}")
                if ref and alt:
                    variant_info.append(f"Change: {ref}>{alt}")
                
                # Variant type and consequence
                var_type = variant.get("variant_type", "")
                consequence = variant.get("consequence", "")
                if var_type:
                    variant_info.append(f"Type: {var_type}")
                if consequence and consequence != "Unknown":
                    variant_info.append(f"Consequence: {consequence}")
                
                # Metrics: VAF, AF, DP
                vaf = variant.get("vaf")
                af = variant.get("af")
                dp = variant.get("dp")
                
                metrics = []
                if vaf is not None:
                    metrics.append(f"VAF: {vaf:.2%}")
                if af is not None:
                    metrics.append(f"AF: {af:.4f}")
                elif variant.get("is_rare", False):
                    metrics.append("AF: Rare (<0.01)")
                if dp is not None:
                    metrics.append(f"DP: {dp}")
                
                if metrics:
                    variant_info.append(f"Metrics: {', '.join(metrics)}")
                
                # Clinical significance
                clinvar = variant.get("clinvar", "")
                if clinvar and clinvar != "Not reported":
                    variant_info.append(f"ClinVar: {clinvar}")
                
                # Impact indicators
                impact_indicators = []
                if variant.get("is_pathogenic", False):
                    impact_indicators.append("Pathogenic")
                if variant.get("is_high_impact", False):
                    impact_indicators.append("High Impact")
                if variant.get("hotspot", False):
                    impact_indicators.append("Hotspot")
                if variant.get("is_cancer_gene", False):
                    impact_indicators.append("Cancer Gene")
                
                impact_score = variant.get("impact_score")
                if impact_score is not None:
                    impact_indicators.append(f"Impact Score: {impact_score:.2f}")
                
                if impact_indicators:
                    variant_info.append(f"Flags: {', '.join(impact_indicators)}")

                prompt_parts.append(f"{i}. {' | '.join(variant_info)}")

        prompt_parts.append("\n" + "-" * 50)
        prompt_parts.append(
            "Prescriptive clinical analysis of the breast cancer panel. "
            "Pathogenic variants only."
        )
        prompt_parts.append(GenomicPromptTemplates.PRESCRIPTIVE_SUFFIX)

        return "\n".join(prompt_parts)

    @staticmethod
    def format_user_prompt_compact(
        patient_id: str,
        variants: List[Dict],
        coverage: float,
    ) -> str:
        """
        Format user prompt in compact format (for training data).

        Args:
            patient_id: Patient identifier
            variants: List of variant dictionaries
            coverage: Sequencing coverage depth

        Returns:
            Compact formatted prompt string
        """
        if not variants:
            return f"Patient {patient_id} has no detected variants. Coverage: {coverage:.1f}x"
        
        # Extract gene names and VAFs
        gene_vafs = []
        for variant in variants:
            gene = variant.get("gene", "Unknown")
            vaf = variant.get("vaf")
            if vaf is not None:
                gene_vafs.append(f"{gene} (VAF: {vaf:.2f})")
            else:
                gene_vafs.append(gene)
        
        variant_count = len(variants)
        genes_str = ", ".join(gene_vafs)
        
        prompt = f"Patient {patient_id} has {variant_count} variant{'s' if variant_count > 1 else ''}: {genes_str}, with coverage {coverage:.1f}x"
        
        return prompt

    @staticmethod
    def format_training_prompt(
        patient_id: str,
        variants: List[Dict],
        expected_output: str,
    ) -> str:
        """
        Format prompt for training data.

        Args:
            patient_id: Patient identifier
            variants: List of variant dictionaries
            expected_output: Expected model output/analysis

        Returns:
            Formatted training prompt
        """
        user_prompt = GenomicPromptTemplates.format_user_prompt(
            patient_id=patient_id,
            variants=variants,
            coverage=30.0,  # Default coverage for training
        )

        return f"{user_prompt}\n\nExpected Analysis:\n{expected_output}"



