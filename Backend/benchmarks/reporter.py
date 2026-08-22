"""
reporter.py — Generation de rapports (JSON, CSV, HTML).

Produit des rapports exploitables dans un article de doctorat:
- JSON : donnees brutes pour traitement ulterieur (R, Python)
- CSV  : import direct dans Excel / LaTeX (pgfplots, booktabs)
- HTML : rapport visuel interactif avec graphiques Plotly
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.config import BenchmarkConfig
from benchmarks.metrics import LatencyStats


class BenchmarkReporter:
    """
    Sauvegarde les resultats et genere les rapports.

    Formats supportes:
      - json  : donnees brutes completes
      - csv   : une ligne par endpoint
      - html  : rapport interactif avec graphiques
    """

    def __init__(self, cfg: BenchmarkConfig) -> None:
        self.cfg = cfg
        self.results_dir = Path(cfg.results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Point d'entree principal
    # ------------------------------------------------------------------

    def save(
        self,
        stats: List[LatencyStats],
        benchmark_type: str = "latency",
        label: Optional[str] = None,
    ) -> None:
        """Sauvegarde dans tous les formats configures."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        stem = f"bench_{benchmark_type}_{ts}"
        if label:
            stem = f"{stem}_{label}"

        for fmt in self.cfg.report_format:
            if fmt == "json":
                self._save_json(stats, stem)
            elif fmt == "csv":
                self._save_csv(stats, stem)
            elif fmt == "html":
                self._save_html(stats, stem, benchmark_type)
            else:
                logger.warning(f"Format inconnu ignore: {fmt}")

    # ------------------------------------------------------------------
    # JSON
    # ------------------------------------------------------------------

    def _save_json(self, stats: List[LatencyStats], stem: str) -> None:
        path = self.results_dir / f"{stem}.json"
        payload = {
            "benchmark_meta": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "base_url": self.cfg.base_url,
                "n_iterations": self.cfg.n_iterations,
                "n_warmup": self.cfg.n_warmup,
                "sla": {
                    "p50_ms": self.cfg.sla_p50_ms,
                    "p95_ms": self.cfg.sla_p95_ms,
                    "p99_ms": self.cfg.sla_p99_ms,
                    "error_rate_pct": self.cfg.sla_error_rate_pct,
                },
            },
            "results": [s.to_dict() for s in stats],
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"Rapport JSON : {path}")

    # ------------------------------------------------------------------
    # CSV
    # ------------------------------------------------------------------

    def _save_csv(self, stats: List[LatencyStats], stem: str) -> None:
        path = self.results_dir / f"{stem}.csv"
        fields = [
            "endpoint", "method", "n_total", "n_success", "n_error",
            "error_rate_pct",
            "min_ms", "mean_ms", "median_p50_ms", "p75_ms", "p90_ms",
            "p95_ms", "p99_ms", "max_ms", "std_ms",
            "throughput_rps", "total_duration_s",
            "sla_p50_ok", "sla_p95_ok", "sla_p99_ok", "sla_error_rate_ok", "sla_pass",
        ]
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for s in stats:
                writer.writerow({
                    "endpoint": s.endpoint,
                    "method": s.method,
                    "n_total": s.n_total,
                    "n_success": s.n_success,
                    "n_error": s.n_error,
                    "error_rate_pct": round(s.error_rate_pct, 2),
                    "min_ms": round(s.min_ms, 2),
                    "mean_ms": round(s.mean_ms, 2),
                    "median_p50_ms": round(s.median_ms, 2),
                    "p75_ms": round(s.p75_ms, 2),
                    "p90_ms": round(s.p90_ms, 2),
                    "p95_ms": round(s.p95_ms, 2),
                    "p99_ms": round(s.p99_ms, 2),
                    "max_ms": round(s.max_ms, 2),
                    "std_ms": round(s.std_ms, 2),
                    "throughput_rps": round(s.throughput_rps, 2),
                    "total_duration_s": round(s.total_duration_s, 3),
                    "sla_p50_ok": s.sla_p50_ok,
                    "sla_p95_ok": s.sla_p95_ok,
                    "sla_p99_ok": s.sla_p99_ok,
                    "sla_error_rate_ok": s.sla_error_rate_ok,
                    "sla_pass": s.sla_pass,
                })
        logger.info(f"Rapport CSV : {path}")

    # ------------------------------------------------------------------
    # HTML (interactif avec graphiques)
    # ------------------------------------------------------------------

    def _save_html(
        self,
        stats: List[LatencyStats],
        stem: str,
        benchmark_type: str,
    ) -> None:
        path = self.results_dir / f"{stem}.html"
        try:
            html = self._build_html(stats, benchmark_type)
            path.write_text(html, encoding="utf-8")
            logger.info(f"Rapport HTML : {path}")
        except ImportError:
            logger.warning("plotly non installe — rapport HTML ignore. pip install plotly")

    def _build_html(self, stats: List[LatencyStats], benchmark_type: str) -> str:
        """Construit un rapport HTML autonome avec graphiques Plotly inline."""
        try:
            import plotly.graph_objects as go
            from plotly.subplots import make_subplots
            import plotly.io as pio
        except ImportError:
            raise

        endpoints = [s.endpoint for s in stats]
        colors = ["#2196F3", "#4CAF50", "#FF9800", "#F44336", "#9C27B0"]

        # ── Graphique 1 : Box plot latence par endpoint ──────────────────
        fig = make_subplots(
            rows=2, cols=2,
            subplot_titles=[
                "Distribution de latence (percentiles)",
                "Throughput par endpoint (req/s)",
                "Taux d'erreur (%)",
                "Latence p50 / p95 / p99",
            ],
        )

        # Bar chart percentiles
        for metric, color, label in [
            ("median_ms", "#64B5F6", "p50"),
            ("p95_ms", "#FF9800", "p95"),
            ("p99_ms", "#F44336", "p99"),
        ]:
            fig.add_trace(
                go.Bar(
                    name=label,
                    x=endpoints,
                    y=[getattr(s, metric) for s in stats],
                    marker_color=color,
                    text=[f"{getattr(s, metric):.1f}ms" for s in stats],
                    textposition="auto",
                ),
                row=1, col=1,
            )

        # Throughput
        fig.add_trace(
            go.Bar(
                name="RPS",
                x=endpoints,
                y=[s.throughput_rps for s in stats],
                marker_color="#4CAF50",
                text=[f"{s.throughput_rps:.2f}" for s in stats],
                textposition="auto",
                showlegend=False,
            ),
            row=1, col=2,
        )

        # Taux d'erreur
        fig.add_trace(
            go.Bar(
                name="Erreurs %",
                x=endpoints,
                y=[s.error_rate_pct for s in stats],
                marker_color=[
                    "#F44336" if s.error_rate_pct > self.cfg.sla_error_rate_pct else "#4CAF50"
                    for s in stats
                ],
                text=[f"{s.error_rate_pct:.1f}%" for s in stats],
                textposition="auto",
                showlegend=False,
            ),
            row=2, col=1,
        )

        # Ligne SLA p95
        fig.add_hline(
            y=self.cfg.sla_p95_ms,
            line_dash="dash",
            line_color="red",
            annotation_text=f"SLA p95={self.cfg.sla_p95_ms:.0f}ms",
            row=1, col=1,
        )

        # Scatter min/mean/max
        for metric, color, name in [
            ("min_ms", "#4CAF50", "Min"),
            ("mean_ms", "#2196F3", "Moyenne"),
            ("max_ms", "#F44336", "Max"),
        ]:
            fig.add_trace(
                go.Scatter(
                    name=name,
                    x=endpoints,
                    y=[getattr(s, metric) for s in stats],
                    mode="lines+markers",
                    marker_color=color,
                ),
                row=2, col=2,
            )

        fig.update_layout(
            title_text=f"Benchmark Zaynb — {benchmark_type.upper()} — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            height=800,
            barmode="group",
            template="plotly_white",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )

        # ── Tableau HTML des stats ────────────────────────────────────────
        rows_html = ""
        for s in stats:
            badge = '<span style="color:green">✅ OK</span>' if s.sla_pass else '<span style="color:red">❌ KO</span>'
            rows_html += (
                f"<tr>"
                f"<td>{s.endpoint}</td>"
                f"<td>{s.n_total}</td>"
                f"<td>{s.error_rate_pct:.1f}%</td>"
                f"<td>{s.min_ms:.1f}</td>"
                f"<td>{s.mean_ms:.1f}</td>"
                f"<td>{s.median_ms:.1f}</td>"
                f"<td>{s.p95_ms:.1f}</td>"
                f"<td>{s.p99_ms:.1f}</td>"
                f"<td>{s.max_ms:.1f}</td>"
                f"<td>{s.throughput_rps:.2f}</td>"
                f"<td>{badge}</td>"
                f"</tr>"
            )

        chart_html = pio.to_html(fig, full_html=False, include_plotlyjs="cdn")

        html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <title>Benchmark Zaynb — {benchmark_type}</title>
  <style>
    body {{ font-family: 'Segoe UI', sans-serif; margin: 2rem; background: #fafafa; color: #222; }}
    h1 {{ color: #1565C0; }}
    h2 {{ color: #37474F; border-bottom: 2px solid #BBDEFB; padding-bottom: 4px; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; }}
    th {{ background: #1565C0; color: white; padding: 8px 12px; text-align: left; }}
    td {{ padding: 7px 12px; border-bottom: 1px solid #E0E0E0; }}
    tr:hover {{ background: #E3F2FD; }}
    .meta {{ background: #E3F2FD; border-left: 4px solid #1565C0; padding: 1rem; border-radius: 4px; margin-bottom: 1.5rem; }}
    .sla-box {{ display: inline-block; padding: 4px 12px; border-radius: 4px; margin: 4px; }}
    .sla-ok  {{ background: #C8E6C9; color: #1B5E20; }}
    .sla-ko  {{ background: #FFCDD2; color: #B71C1C; }}
  </style>
</head>
<body>
  <h1>📊 Benchmark Zaynb Genomic Backend</h1>
  <div class="meta">
    <strong>Type :</strong> {benchmark_type} &nbsp;|&nbsp;
    <strong>URL :</strong> {self.cfg.base_url} &nbsp;|&nbsp;
    <strong>Date :</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} &nbsp;|&nbsp;
    <strong>Iterations :</strong> {self.cfg.n_iterations} (+{self.cfg.n_warmup} warm-up)
    <br><br>
    <strong>SLA cibles :</strong>
    <span class="sla-box sla-ok">p50 &le; {self.cfg.sla_p50_ms:.0f} ms</span>
    <span class="sla-box sla-ok">p95 &le; {self.cfg.sla_p95_ms:.0f} ms</span>
    <span class="sla-box sla-ok">p99 &le; {self.cfg.sla_p99_ms:.0f} ms</span>
    <span class="sla-box sla-ok">Erreurs &le; {self.cfg.sla_error_rate_pct:.1f}%</span>
  </div>

  <h2>Graphiques</h2>
  {chart_html}

  <h2>Tableau des resultats (ms)</h2>
  <table>
    <thead>
      <tr>
        <th>Endpoint</th><th>N</th><th>Erreurs</th>
        <th>Min</th><th>Moy</th><th>p50</th><th>p95</th><th>p99</th><th>Max</th>
        <th>RPS</th><th>SLA</th>
      </tr>
    </thead>
    <tbody>
      {rows_html}
    </tbody>
  </table>
</body>
</html>"""
        return html

    # ------------------------------------------------------------------
    # Synthese console
    # ------------------------------------------------------------------

    def print_summary(self, stats: List[LatencyStats]) -> None:
        """Affiche un recapitulatif global dans le terminal."""
        n_pass = sum(1 for s in stats if s.sla_pass)
        n_fail = len(stats) - n_pass

        print(f"\n{'═' * 60}")
        print(f"  SYNTHESE FINALE")
        print(f"{'═' * 60}")
        print(f"  Endpoints testes  : {len(stats)}")
        print(f"  SLA respectes     : {n_pass}")
        print(f"  SLA non respectes : {n_fail}")
        print(f"{'─' * 60}")
        for s in stats:
            badge = "✅" if s.sla_pass else "❌"
            print(f"  {badge}  {s.endpoint:<45}  p95={s.p95_ms:6.1f}ms")
        print(f"{'═' * 60}\n")
        print(f"  Rapports sauvegardes dans : {self.results_dir.resolve()}\n")
