"""Parabricks configuration module."""

import os
from typing import Optional
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class ParabricksConfig:
    """Configuration for NVIDIA Parabricks GPU-accelerated genomics pipeline."""

    image: str
    license_key: Optional[str] = None
    gpu_count: int = 1
    memory_gb: int = 48
    shm_size: str = "8g"
    # GPU 16 Go : --low-memory si fq2bam manque de VRAM (n'affecte que l'usage mémoire)
    low_memory: bool = False

    @classmethod
    def from_env(cls) -> "ParabricksConfig":
        """Create ParabricksConfig from environment variables."""
        return cls(
            image=os.getenv(
                "PARABRICKS_IMAGE",
                "nvcr.io/nvidia/clara/clara-parabricks:4.6.0-1",
            ),
            license_key=os.getenv("PARABRICKS_LICENSE_KEY"),
            gpu_count=int(os.getenv("PARABRICKS_GPU_COUNT", "1")),
            memory_gb=int(os.getenv("PARABRICKS_MEMORY_GB", "48")),
            shm_size=os.getenv("PARABRICKS_SHM_SIZE", "8g"),
            low_memory=os.getenv("PARABRICKS_LOW_MEMORY", "false").lower() in ("1", "true", "yes"),
        )

    def get_docker_command(
        self,
        command: str,
        input_files: list[str],
        output_file: str,
        reference_genome: str,
        use_gpu: Optional[bool] = None,
        **kwargs: str,
    ) -> list[str]:
        """
        Generate Docker command for Parabricks execution.
        
        Parabricks supports direct S3 URIs (s3://bucket/key) without
        needing to download files first. See:
        https://docs.nvidia.com/clara/parabricks/latest/tutorials/fq2bam_tutorial.html

        Args:
            command: Parabricks command (fq2bam, haplotypecaller, etc.)
            input_files: List of input file paths (can be S3 URIs)
            output_file: Output file path (can be S3 URI)
            reference_genome: Path to reference genome FASTA (can be S3 URI)
            use_gpu: Whether to use GPU (None = auto-detect, True = force GPU, False = CPU only)
            **kwargs: Additional command-line arguments

        Returns:
            List of command arguments for subprocess
        """
        from config.runtime_config import runtime_config

        cmd = [
            "docker",
            "run",
            "--rm",
            "--name",
            f"parabricks-{command}",
            f"--memory={self.memory_gb}g",
            f"--shm-size={self.shm_size}",
        ]

        if use_gpu:
            cmd.extend(["--gpus", "all"])

        cmd.extend(runtime_config.docker_volume_args())
        cmd.extend([
            "-e", "NVIDIA_VISIBLE_DEVICES=all",
            "-e", "NVIDIA_DRIVER_CAPABILITIES=compute,utility",
            self.image,
            "pbrun",
            command,
        ])

        if command in ("fq2bam", "haplotypecaller", "bqsr", "markdup"):
            cmd.extend(["--ref", reference_genome])
        if command == "fq2bam":
            cmd.extend(["--in-fq"] + input_files)
            cmd.extend(["--out-bam", output_file])
            if self.low_memory:
                cmd.append("--low-memory")
        elif command == "markdup":
            cmd.extend(["--in-bam"] + input_files)
            cmd.extend(["--out-bam", output_file])
        elif command == "bqsr":
            cmd.extend(["--in-bam"] + input_files)
            cmd.extend(["--out-bam", output_file])
        elif command == "haplotypecaller":
            cmd.extend(["--in-bam"] + input_files)
            cmd.extend(["--out-variants", output_file])

        # Add additional kwargs
        for key, value in kwargs.items():
            cmd.extend([f"--{key.replace('_', '-')}", str(value)])

        return cmd


# Global instance
parabricks_config = ParabricksConfig.from_env()

