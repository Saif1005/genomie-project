"""VariantAnnotationAgent — reads the VCF over the panel regions and annotates it with ClinVar."""

from __future__ import annotations

import os
from typing import Any, Dict

from config.settings import panel_padding
from src.agents._io import patient_output_dir, sha256_file, write_artifact
from src.core import context as K
from src.core.agent import AgentError, AgentResult, BaseAgent
from src.genomics import AnnotationUnavailable, VCFFormatError, build_annotator, get_panel, iter_variants, read_header
from src.genomics.statistics import summarize_file
from src.storage import StorageError, get_storage


class VariantAnnotationAgent(BaseAgent):
    def __init__(self, config: Dict[str, Any] | None = None):
        super().__init__("VariantAnnotation", config)

    def execute(self, context: Dict[str, Any]) -> AgentResult:
        pid, uri = context[K.PATIENT_ID], context[K.VCF_URI]
        try:
            vcf = get_storage().fetch(uri, patient_output_dir(pid) / os.path.basename(uri))
        except StorageError as e:
            raise AgentError(f"VCF not accessible: {e}") from e

        panel = get_panel()
        try:
            header = read_header(vcf)
            annotator = build_annotator(header.has_clinvar_annotation, panel)
        except AnnotationUnavailable as e:
            raise AgentError(str(e)) from e
        except (OSError, VCFFormatError) as e:
            raise AgentError(f"VCF unreadable: {e}") from e

        regions = [(c, s, e) for c, s, e, _ in panel.intervals(padding=panel_padding())]
        entries = []
        try:
            for v in iter_variants(vcf, regions):
                rec = annotator.annotate(v)
                entries.append({"variant": v.to_dict(), "clinvar": rec.to_dict() if rec else None})
        except VCFFormatError as e:
            raise AgentError(f"Malformed VCF: {e}") from e

        try:
            file_summary = summarize_file(iter_variants(vcf))  # whole VCF, streamed
        except VCFFormatError as e:
            raise AgentError(f"Malformed VCF: {e}") from e

        meta = {
            "source": annotator.name,
            "version": annotator.version,
            "panel_version": panel.version,
            "vcf_sha256": sha256_file(vcf),
            "vcf_samples": header.samples,
            "variants_read": len(entries),
            "annotated": sum(1 for e in entries if e["clinvar"]),
        }
        self.logger.info(
            f"{meta['variants_read']} alleles read in the panel, {meta['annotated']} annotated "
            f"({meta['source']} {meta['version']})"
        )
        artifact = write_artifact(
            pid,
            "annotated_variants.json",
            {"patient_id": pid, "annotation": meta, "file_summary": file_summary, "variants": entries},
        )
        return AgentResult.ok(**{K.ANNOTATED_VARIANTS: artifact, K.ANNOTATION: meta, K.INPUT_SHA256: meta["vcf_sha256"]})
