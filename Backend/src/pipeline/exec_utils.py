"""Exécution locale (serveur on-premise ou EC2 courant) sans SSH / DescribeInstances."""

from __future__ import annotations

import os
import subprocess
import urllib.request
from typing import Optional, Tuple


def get_metadata_instance_id(timeout: float = 2.0) -> Optional[str]:
    from config.deployment import is_local

    if is_local():
        return None  # pas de service de métadonnées EC2 hors AWS
    try:
        return (
            urllib.request.urlopen(
                "http://169.254.169.254/latest/meta-data/instance-id",
                timeout=timeout,
            )
            .read()
            .decode("utf-8")
            .strip()
        )
    except Exception:
        return None


def is_local_ec2_instance(target_instance_id: Optional[str]) -> bool:
    from config.deployment import is_local

    if is_local():
        return True  # DEPLOYMENT_MODE=local : toujours exécution sur cette machine
    if os.getenv("RUN_ON_EC2", "").lower() in ("1", "true", "yes"):
        return True
    local_id = get_metadata_instance_id()
    return bool(local_id and target_instance_id and local_id == target_instance_id)


def execute_command(
    command: str, timeout: Optional[int] = None, local: bool = False
) -> Tuple[int, str, str]:
    if local:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    raise RuntimeError("Remote execution requires runner SSH path")
