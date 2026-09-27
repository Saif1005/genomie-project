"""Parabricks / GATK4 CPU choice according to the VRAM actually detected."""

import pytest

import src.utils.gpu_manager as gm


@pytest.mark.parametrize(
    "vram_mb,expected",
    [
        (15360, "parabricks"),  # "16 GB" card (15,360 MiB exposed)
        (16384, "parabricks"),
        (24576, "parabricks"),
        (12288, "cpu"),
        (4096, "cpu"),
    ],
)
def test_backend_from_vram(monkeypatch, vram_mb, expected):
    monkeypatch.delenv("PIPELINE_BACKEND", raising=False)
    monkeypatch.setattr(
        gm, "_gpu_inventory_cache", [{"name": "GPU", "vram_mb": float(vram_mb), "compute_cap": None}]
    )
    assert gm.select_pipeline_backend()["backend"] == expected


def test_no_gpu_means_cpu(monkeypatch):
    monkeypatch.delenv("PIPELINE_BACKEND", raising=False)
    monkeypatch.setattr(gm, "_gpu_inventory_cache", [])
    assert gm.select_pipeline_backend()["backend"] == "cpu"


def test_forced_backend(monkeypatch):
    monkeypatch.setenv("PIPELINE_BACKEND", "cpu")
    monkeypatch.setattr(
        gm, "_gpu_inventory_cache", [{"name": "GPU", "vram_mb": 49152.0, "compute_cap": None}]
    )
    assert gm.select_pipeline_backend()["backend"] == "cpu"
