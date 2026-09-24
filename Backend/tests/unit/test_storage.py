"""Tests de la couche StorageBackend (mode local)."""

import pytest

from src.storage.base import StorageError
from src.storage.local import LocalStorage


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(tmp_path)


def test_validate_accepts_file_under_root(storage, tmp_path):
    f = tmp_path / "patients" / "P1" / "input" / "R1.fastq.gz"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"@r\nACGT\n+\nIIII\n")
    assert storage.validate_input_uri(str(f)) == str(f)
    # Chemin relatif à la racine
    assert storage.validate_input_uri("patients/P1/input/R1.fastq.gz") == str(f)


def test_validate_rejects_outside_root(storage, tmp_path):
    with pytest.raises(ValueError, match="hors de LOCAL_DATA_ROOT"):
        storage.validate_input_uri("/etc/passwd")
    with pytest.raises(ValueError, match="hors de LOCAL_DATA_ROOT"):
        storage.validate_input_uri("patients/../../etc/passwd")


def test_validate_rejects_s3_and_missing(storage):
    with pytest.raises(ValueError, match="S3 non supportée"):
        storage.validate_input_uri("s3://bucket/key.vcf")
    with pytest.raises(ValueError, match="introuvable"):
        storage.validate_input_uri("patients/P1/input/absent.vcf")


def test_put_copies_and_move(storage, tmp_path):
    src = tmp_path.parent / f"{tmp_path.name}_src.json"
    src.write_text("{}")
    key = storage.key_for("P1", "report", "r.json")
    assert key == "patients/P1/output/r.json"
    uri = storage.put(str(src), key)
    assert uri == str(tmp_path / key)
    assert src.exists()
    # put sur lui-même = no-op
    assert storage.put(uri, key) == uri
    moved = storage.put(str(src), storage.key_for("P1", "input", "m.json"), move=True)
    assert not src.exists() and (tmp_path / "patients/P1/input/m.json").exists()
    assert storage.is_managed(moved)


def test_fetch_reads_in_place(storage, tmp_path):
    f = tmp_path / "patients" / "P1" / "input" / "v.vcf"
    f.parent.mkdir(parents=True)
    f.write_text("##fileformat=VCFv4.2\n")
    assert storage.fetch(str(f), tmp_path / "ignored") == str(f)
    with pytest.raises(StorageError):
        storage.fetch("patients/P1/input/none.vcf", tmp_path / "x")
