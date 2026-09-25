"""Niveau de risque héréditaire — règles explicites, versionnées, sans modèle de langage.

  HIGH          variant P/LP confirmé dans un gène à haute pénétrance
  MODERATE      variant P/LP confirmé dans un gène à pénétrance modérée, ou allèle « low penetrance »
  INDETERMINATE aucun variant confirmé mais au moins un variant à confirmer (QC insuffisante,
                perte de fonction non classée) — ne jamais rassurer à tort
  LOW           aucun variant P/LP ni à confirmer sur les gènes germinaux du panel
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Dict, List

METHOD = "zaynb-rules-v1"


class RiskLevel(str, Enum):
    HIGH = "HIGH"
    MODERATE = "MODERATE"
    LOW = "LOW"
    INDETERMINATE = "INDETERMINATE"


CONCLUSIONS = {
    RiskLevel.HIGH: "Risque génétique de cancer du sein : ÉLEVÉ",
    RiskLevel.MODERATE: "Risque génétique de cancer du sein : MODÉRÉ",
    RiskLevel.LOW: "Risque génétique de cancer du sein : FAIBLE (sur les gènes et variants analysés)",
    RiskLevel.INDETERMINATE: "Risque génétique de cancer du sein : INDÉTERMINÉ — confirmation requise",
}

LIMITATIONS = (
    "Seuls les variants ponctuels et petits indels sont analysés : les grands réarrangements et "
    "variations du nombre de copies (ex. délétions d'exons BRCA1) ne sont pas détectés.",
    "La classification repose sur ClinVar ; un variant absent de ClinVar n'est signalé que s'il "
    "est annoté perte de fonction dans le VCF.",
    "La couverture des régions du panel n'est pas mesurée à partir du VCF : une absence de variant "
    "ne vaut pas absence de mutation dans une région mal couverte.",
    "Les gènes somatiques (PIK3CA, ERBB2/HER2, MYC) sont hors du périmètre d'un appel germinal.",
)


@dataclass(frozen=True)
class RiskAssessment:
    level: RiskLevel
    conclusion: str
    rationale: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=lambda: list(LIMITATIONS))
    method: str = METHOD

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["level"] = self.level.value
        return d


def _describe(f: Dict) -> str:
    stars = f.get("review_stars")
    stars_txt = f", {stars}★ ClinVar" if stars is not None else ""
    return (
        f"{f['gene']} {f['chromosome']}:{f['position']} {f['mutation']}"
        f" ({f.get('clinvar_significance') or 'non classé'}{stars_txt}, "
        f"{f.get('zygosity') or 'zygotie inconnue'}, pénétrance {f.get('penetrance')})"
    )


def assess_risk(analysis: Dict) -> RiskAssessment:
    confirmed: List[Dict] = analysis.get("confirmed", [])
    to_confirm: List[Dict] = analysis.get("to_confirm", [])
    rationale: List[str] = []

    high = [f for f in confirmed if f.get("penetrance") == "high"]
    other = [f for f in confirmed if f.get("penetrance") != "high"]
    for f in high + other:
        rationale.append(f"Variant confirmé : {_describe(f)}")
    for f in to_confirm:
        reason = f.get("note") or "contrôle qualité insuffisant (" + ", ".join(f.get("qc_flags", [])) + ")"
        rationale.append(f"À confirmer : {_describe(f)} — {reason}")

    if high:
        level = RiskLevel.HIGH
    elif other:
        level = RiskLevel.MODERATE
    elif to_confirm:
        level = RiskLevel.INDETERMINATE
    else:
        level = RiskLevel.LOW
        rationale.append(
            f"Aucun variant pathogène ou probablement pathogène sur {len(analysis.get('panel_genes', []))} "
            f"gènes germinaux ({analysis.get('variants_in_panel', 0)} variants appelés dans le panel)."
        )
    if analysis.get("vus"):
        rationale.append(f"{len(analysis['vus'])} variant(s) de signification incertaine (VUS), non retenus pour le risque.")
    if analysis.get("conflicting"):
        rationale.append(f"{len(analysis['conflicting'])} variant(s) à classification ClinVar conflictuelle, à revoir.")
    rationale.append(
        f"Annotation : {analysis.get('annotation_source')} (version {analysis.get('annotation_version')}), "
        f"panel {analysis.get('panel_version')}, méthode {METHOD}."
    )
    return RiskAssessment(level=level, conclusion=CONCLUSIONS[level], rationale=rationale)
