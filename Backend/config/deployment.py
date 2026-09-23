"""Mode de déploiement : `local` (serveur on-premise) ou `aws` (EC2 + S3).

DEPLOYMENT_MODE=local  → stockage fichiers sous LOCAL_DATA_ROOT, aucun appel AWS.
DEPLOYMENT_MODE=aws    → comportement historique (S3, EC2, IAM).

Lu à chaque appel (pas de cache) pour que les tests puissent basculer de mode.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

LOCAL = "local"
AWS = "aws"


def deployment_mode() -> str:
    mode = os.getenv("DEPLOYMENT_MODE", LOCAL).strip().lower()
    return AWS if mode == AWS else LOCAL


def is_local() -> bool:
    return deployment_mode() == LOCAL


def local_data_root() -> Path:
    """Racine des données locales (référence, patients, modèles, tmp)."""
    return Path(os.getenv("LOCAL_DATA_ROOT", "/data/zaynb")).resolve()
