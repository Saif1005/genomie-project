"""Configuration runtime production — chemins absolus (EBS en mode aws, disque local sinon)."""

import os
from pathlib import Path
from pydantic import BaseModel, Field, field_validator

from config.deployment import is_local, local_data_root


def _default_mount(env_var: str, aws_default: str, local_subdir: str) -> Path:
    """Variable d'env explicite > défaut du mode (local: LOCAL_DATA_ROOT/…, aws: /mnt/data/…)."""
    explicit = os.getenv(env_var)
    if explicit:
        return Path(explicit)
    if is_local():
        return local_data_root() / local_subdir if local_subdir else local_data_root()
    return Path(aws_default)


class RuntimeConfig(BaseModel):
    """Chemins montés sur l'hôte (identiques dans le conteneur backend et les conteneurs Parabricks/GATK)."""

    data_mount: Path = Field(
        default_factory=lambda: _default_mount("RUNTIME_DATA_MOUNT", "/mnt/data", "")
    )
    ref_mount: Path = Field(
        default_factory=lambda: _default_mount(
            "RUNTIME_REF_MOUNT", "/mnt/data/references", "reference"
        )
    )
    scratch_mount: Path = Field(
        default_factory=lambda: _default_mount(
            "RUNTIME_SCRATCH_MOUNT", "/mnt/data/scratch", "tmp/scratch"
        )
    )
    work_mount: Path = Field(
        default_factory=lambda: _default_mount(
            "RUNTIME_WORK_MOUNT", "/mnt/data/work", "tmp/work"
        )
    )

    @field_validator("data_mount", "ref_mount", "scratch_mount", "work_mount")
    @classmethod
    def _absolute_only(cls, v: Path) -> Path:
        p = Path(v).resolve()
        if not p.is_absolute():
            raise ValueError(f"Chemin absolu requis: {v}")
        return p

    def docker_volume_args(self) -> list[str]:
        """Arguments -v Docker pour tous les montages runtime."""
        mounts = {self.data_mount, self.ref_mount, self.scratch_mount, self.work_mount}
        args: list[str] = []
        for m in sorted(mounts):
            args.extend(["-v", f"{m}:{m}"])
        return args


runtime_config = RuntimeConfig()
