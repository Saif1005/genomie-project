"""Parabricks runner module for executing Parabricks commands on EC2.

Parabricks supports direct S3 URIs without needing to download/upload files.
See: https://docs.nvidia.com/clara/parabricks/latest/tutorials/fq2bam_tutorial.html
"""

from typing import Optional
from loguru import logger
import paramiko
import shlex

from config.parabricks_config import parabricks_config
from src.pipeline.exec_utils import is_local_ec2_instance, execute_command

# Try to import CPURunner for fallback, but make it optional
try:
    from src.pipeline.cpu_runner import CPURunner
    CPU_RUNNER_AVAILABLE = True
except ImportError:
    CPU_RUNNER_AVAILABLE = False
    logger.warning("CPURunner not available - GPU fallback will not work")


class ParabricksRunnerError(Exception):
    """Custom exception for Parabricks execution errors."""

    pass


class ParabricksRunner:
    """Runner for executing Parabricks commands on EC2 GPU instances."""

    def __init__(
        self,
        instance_id: Optional[str] = None,
        ssh_key_path: Optional[str] = None,
        ssh_user: str = "ubuntu",
    ):
        """
        Initialize Parabricks runner.

        Args:
            instance_id: Optional EC2 instance ID (will launch if not provided)
            ssh_key_path: Path to SSH private key
            ssh_user: SSH username (default: ubuntu)
        """
        self.instance_id = instance_id
        self.ssh_key_path = ssh_key_path
        self.ssh_user = ssh_user
        self.local_mode = is_local_ec2_instance(instance_id)
        if self.local_mode:
            logger.info("ParabricksRunner: exécution locale (sans SSH)")
            if not self.instance_id:
                from src.pipeline.exec_utils import get_metadata_instance_id
                self.instance_id = get_metadata_instance_id() or "local"
        else:
            from src.aws.ec2_manager import get_ec2_manager  # boto3 : mode aws uniquement

            self.ec2_manager = get_ec2_manager()
        self.ssh_client: Optional[paramiko.SSHClient] = None

    def _ensure_instance_running(self) -> str:
        """
        Ensure EC2 instance is running, launch if needed.

        Returns:
            Instance ID
        """
        if self.instance_id:
            if self.local_mode:
                return self.instance_id
            # Check if instance is running
            info = self.ec2_manager.get_instance_info(self.instance_id)
            if info["state"] == "running":
                return self.instance_id
            elif info["state"] == "stopped":
                # Start stopped instance
                logger.info(f"Starting stopped instance: {self.instance_id}")
                self.ec2_manager.ec2_client.start_instances(
                    InstanceIds=[self.instance_id]
                )
                self.ec2_manager.wait_for_instance(self.instance_id, "running")
                return self.instance_id

        # Launch new instance
        logger.info("Launching new EC2 GPU instance for Parabricks")
        self.instance_id = self.ec2_manager.launch_instance(
            tags={"Name": "parabricks-runner", "Purpose": "genomic-processing"}
        )
        self.ec2_manager.wait_for_instance(self.instance_id, "running")
        return self.instance_id

    def _connect_ssh(self) -> paramiko.SSHClient:
        """
        Establish SSH connection to EC2 instance.

        Returns:
            SSH client

        Raises:
            ParabricksRunnerError: If connection fails
        """
        if self.ssh_client:
            return self.ssh_client

        if self.local_mode:
            return None  # type: ignore

        if not self.ssh_key_path:
            raise ParabricksRunnerError("SSH key path is required")

        instance_info = self.ec2_manager.get_instance_info(self.instance_id)
        public_ip = instance_info.get("public_ip")

        if not public_ip:
            raise ParabricksRunnerError(
                f"Instance {self.instance_id} has no public IP"
            )

        try:
            logger.info(f"Connecting to {public_ip} via SSH")
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            ssh.connect(
                hostname=public_ip,
                username=self.ssh_user,
                key_filename=self.ssh_key_path,
                timeout=30,
            )

            self.ssh_client = ssh
            logger.info("SSH connection established")
            return ssh

        except Exception as e:
            error_msg = f"Failed to connect via SSH: {e}"
            logger.error(error_msg)
            raise ParabricksRunnerError(error_msg) from e

    def _execute_remote_command(
        self, command: str, timeout: Optional[int] = None
    ) -> tuple[int, str, str]:
        """
        Execute command on remote instance via SSH.

        Args:
            command: Command to execute
            timeout: Optional timeout in seconds

        Returns:
            Tuple of (exit_code, stdout, stderr)
        """
        if self.local_mode:
            return execute_command(command, timeout=timeout, local=True)

        ssh = self._connect_ssh()

        try:
            logger.debug(f"Executing remote command: {command}")
            stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout)

            exit_code = stdout.channel.recv_exit_status()
            stdout_text = stdout.read().decode("utf-8")
            stderr_text = stderr.read().decode("utf-8")

            if exit_code != 0:
                logger.warning(
                    f"Command failed with exit code {exit_code}"
                )
                if stderr_text:
                    logger.warning(f"Stderr: {stderr_text[:500]}")
                if stdout_text:
                    logger.debug(f"Stdout: {stdout_text[:500]}")

            return exit_code, stdout_text, stderr_text

        except Exception as e:
            error_msg = f"Failed to execute remote command: {e}"
            logger.error(error_msg)
            raise ParabricksRunnerError(error_msg) from e

    def run_fq2bam(
        self,
        fastq_r1: str,
        fastq_r2: str,
        output_bam: str,
        reference_genome: str,
    ) -> str:
        """
        Run Parabricks fq2bam pipeline (FASTQ to BAM).
        
        Parabricks supports direct S3 URIs, so files don't need to be
        downloaded/uploaded manually. See:
        https://docs.nvidia.com/clara/parabricks/latest/tutorials/fq2bam_tutorial.html

        Args:
            fastq_r1: Path to R1 FASTQ file (local or S3 URI)
            fastq_r2: Path to R2 FASTQ file (local or S3 URI)
            output_bam: Output BAM file path (local or S3 URI)
            reference_genome: Reference genome path (local or S3 URI)

        Returns:
            Path to output BAM file

        Raises:
            ParabricksRunnerError: If execution fails
        """
        # Ensure instance is running
        self._ensure_instance_running()

        logger.info("Starting Parabricks fq2bam pipeline")
        logger.info(f"Input R1: {fastq_r1}")
        logger.info(f"Input R2: {fastq_r2}")
        logger.info(f"Output BAM: {output_bam}")
        logger.info(f"Reference: {reference_genome}")

        # Check if GPU is available on the instance
        logger.info("Checking for GPU availability...")
        gpu_check_cmd = "nvidia-smi --query-gpu=count --format=csv,noheader 2>/dev/null | head -1 || echo '0'"
        exit_code, stdout, stderr = self._execute_remote_command(gpu_check_cmd, timeout=30)
        gpu_count = int(stdout.strip()) if stdout.strip().isdigit() else 0
        
        if gpu_count > 0:
            logger.info(f"✓ GPU detected: {gpu_count} GPU(s) available")
            use_gpu = True
        else:
            logger.warning("⚠ No GPU detected, running in CPU mode (will be slower)")
            use_gpu = False

        # Build Parabricks command with direct S3 URIs
        # Parabricks handles S3 access automatically via IAM role
        docker_cmd = parabricks_config.get_docker_command(
            command="fq2bam",
            input_files=[fastq_r1, fastq_r2],
            output_file=output_bam,
            reference_genome=reference_genome,
            use_gpu=use_gpu,
        )

        # Execute on remote instance
        # Escape command properly for shell execution using shlex
        command = " ".join(shlex.quote(str(arg)) for arg in docker_cmd)
        logger.info(f"Executing Parabricks fq2bam command:")
        logger.info(f"  Full command: {' '.join(str(arg) for arg in docker_cmd)}")
        logger.debug(f"  Escaped command: {command}")
        exit_code, stdout, stderr = self._execute_remote_command(
            command, timeout=86400  # 24 hours (augmenté pour instances CPU)
        )

        # Log output for debugging
        if stdout:
            logger.debug(f"Parabricks stdout: {stdout[:500]}")  # First 500 chars
        if stderr:
            logger.warning(f"Parabricks stderr: {stderr[:500]}")  # First 500 chars

        if exit_code != 0:
            # Check if error is due to missing GPU
            error_lower = (stderr + stdout).lower()
            if "gpu" in error_lower or "cuda" in error_lower or "no accessible gpus" in error_lower:
                if not CPU_RUNNER_AVAILABLE:
                    error_msg = "Parabricks requires GPU and CPU fallback is not available. Please install CPU runner or use a GPU instance."
                    logger.error(error_msg)
                    raise ParabricksRunnerError(error_msg)
                
                logger.warning("⚠ Parabricks requires GPU. Falling back to CPU-native pipeline (BWA-MEM)...")
                logger.info("This will be slower but will work on instances without GPU")
                
                # Use CPU runner as fallback
                cpu_runner = CPURunner(
                    instance_id=self.instance_id,
                    ssh_key_path=self.ssh_key_path,
                    ssh_user=self.ssh_user,
                )
                try:
                    result = cpu_runner.run_fq2bam(
                        fastq_r1=fastq_r1,
                        fastq_r2=fastq_r2,
                        output_bam=output_bam,
                        reference_genome=reference_genome,
                    )
                    cpu_runner.cleanup()
                    logger.info(f"✓ CPU-native pipeline completed successfully: {result}")
                    return result
                except Exception as e:
                    cpu_runner.cleanup()
                    error_msg = f"Both Parabricks and CPU fallback failed. CPU error: {e}"
                    logger.error(error_msg)
                    raise ParabricksRunnerError(error_msg) from e
            
            # Other errors - raise as before
            error_msg = f"Parabricks fq2bam failed with exit code {exit_code}"
            if stderr:
                error_msg += f"\nStderr: {stderr}"
            if stdout:
                error_msg += f"\nStdout: {stdout[:1000]}"  # First 1000 chars
            logger.error(error_msg)
            raise ParabricksRunnerError(error_msg)

        logger.info(f"Parabricks fq2bam completed: {output_bam}")
        return output_bam

    def run_haplotypecaller(
        self,
        input_bam: str,
        output_vcf: str,
        reference_genome: str,
    ) -> str:
        """
        Run Parabricks HaplotypeCaller (variant calling).
        
        Parabricks supports direct S3 URIs, so files don't need to be
        downloaded/uploaded manually.

        Args:
            input_bam: Input BAM file path (local or S3 URI)
            output_vcf: Output VCF file path (local or S3 URI)
            reference_genome: Reference genome path (local or S3 URI)

        Returns:
            Path to output VCF file

        Raises:
            ParabricksRunnerError: If execution fails
        """
        # Ensure instance is running
        self._ensure_instance_running()

        logger.info("Starting Parabricks HaplotypeCaller")
        logger.info(f"Input BAM: {input_bam}")
        logger.info(f"Output VCF: {output_vcf}")
        logger.info(f"Reference: {reference_genome}")

        # Check if GPU is available on the instance
        logger.info("Checking for GPU availability...")
        gpu_check_cmd = "nvidia-smi --query-gpu=count --format=csv,noheader 2>/dev/null | head -1 || echo '0'"
        exit_code, stdout, stderr = self._execute_remote_command(gpu_check_cmd, timeout=30)
        gpu_count = int(stdout.strip()) if stdout.strip().isdigit() else 0
        
        if gpu_count > 0:
            logger.info(f"✓ GPU detected: {gpu_count} GPU(s) available")
            use_gpu = True
        else:
            logger.warning("⚠ No GPU detected, running in CPU mode (will be slower)")
            use_gpu = False

        # Build Parabricks command with direct S3 URIs
        # Parabricks handles S3 access automatically via IAM role
        docker_cmd = parabricks_config.get_docker_command(
            command="haplotypecaller",
            input_files=[input_bam],
            output_file=output_vcf,
            reference_genome=reference_genome,
            use_gpu=use_gpu,
        )

        # Execute on remote instance
        # Escape command properly for shell execution using shlex
        command = " ".join(shlex.quote(str(arg)) for arg in docker_cmd)
        logger.info(f"Executing Parabricks haplotypecaller command:")
        logger.info(f"  Full command: {' '.join(str(arg) for arg in docker_cmd)}")
        logger.debug(f"  Escaped command: {command}")
        exit_code, stdout, stderr = self._execute_remote_command(
            command, timeout=43200  # 12 hours (augmenté pour instances CPU)
        )

        # Log output for debugging
        if stdout:
            logger.debug(f"Parabricks stdout: {stdout[:500]}")  # First 500 chars
        if stderr:
            logger.warning(f"Parabricks stderr: {stderr[:500]}")  # First 500 chars

        if exit_code != 0:
            # Check if error is due to missing GPU
            error_lower = (stderr + stdout).lower()
            if "gpu" in error_lower or "cuda" in error_lower or "no accessible gpus" in error_lower:
                if not CPU_RUNNER_AVAILABLE:
                    error_msg = "Parabricks requires GPU and CPU fallback is not available. Please install CPU runner or use a GPU instance."
                    logger.error(error_msg)
                    raise ParabricksRunnerError(error_msg)
                
                logger.warning("⚠ Parabricks requires GPU. Falling back to CPU-native pipeline (GATK HaplotypeCaller)...")
                logger.info("This will be slower but will work on instances without GPU")
                
                # Use CPU runner as fallback
                cpu_runner = CPURunner(
                    instance_id=self.instance_id,
                    ssh_key_path=self.ssh_key_path,
                    ssh_user=self.ssh_user,
                )
                try:
                    result = cpu_runner.run_haplotypecaller(
                        input_bam=input_bam,
                        output_vcf=output_vcf,
                        reference_genome=reference_genome,
                    )
                    cpu_runner.cleanup()
                    logger.info(f"✓ CPU-native pipeline completed successfully: {result}")
                    return result
                except Exception as e:
                    cpu_runner.cleanup()
                    error_msg = f"Both Parabricks and CPU fallback failed. CPU error: {e}"
                    logger.error(error_msg)
                    raise ParabricksRunnerError(error_msg) from e
            
            # Other errors - raise as before
            error_msg = f"Parabricks haplotypecaller failed with exit code {exit_code}"
            if stderr:
                error_msg += f"\nStderr: {stderr}"
            if stdout:
                error_msg += f"\nStdout: {stdout[:1000]}"  # First 1000 chars
            logger.error(error_msg)
            raise ParabricksRunnerError(error_msg)

        logger.info(f"Parabricks HaplotypeCaller completed: {output_vcf}")
        return output_vcf

    def _run_parabricks_step(
        self,
        command: str,
        input_files: list,
        output_file: str,
        reference_genome: str = "",
        extra_kwargs: Optional[dict] = None,
        timeout: int = 43200,
    ) -> str:
        """Exécute une étape Parabricks GPU (--gpus all, --rm)."""
        self._ensure_instance_running()
        use_gpu = True
        docker_cmd = parabricks_config.get_docker_command(
            command=command,
            input_files=input_files,
            output_file=output_file,
            reference_genome=reference_genome,
            use_gpu=use_gpu,
            **(extra_kwargs or {}),
        )
        shell_cmd = " ".join(shlex.quote(str(arg)) for arg in docker_cmd)
        logger.info(f"Parabricks {command}: {shell_cmd[:300]}...")
        exit_code, stdout, stderr = self._execute_remote_command(shell_cmd, timeout=timeout)
        self._destroy_parabricks_containers()
        if exit_code != 0:
            raise ParabricksRunnerError(
                f"Parabricks {command} failed (exit={exit_code}): {stderr[:2000]}"
            )
        return output_file

    def _destroy_parabricks_containers(self) -> None:
        """Force la destruction des conteneurs Parabricks résiduels."""
        cleanup_cmd = (
            "docker ps -aq --filter name=parabricks- 2>/dev/null | "
            "xargs -r docker rm -f 2>/dev/null || true"
        )
        try:
            self._execute_remote_command(cleanup_cmd, timeout=60)
        except Exception as e:
            logger.warning(f"Container cleanup: {e}")

    def run_markdup(
        self, input_bam: str, output_bam: str, reference_genome: str
    ) -> str:
        """Parabricks MarkDuplicates (GPU)."""
        logger.info(f"Parabricks markdup: {input_bam} -> {output_bam}")
        return self._run_parabricks_step(
            "markdup", [input_bam], output_bam, reference_genome=reference_genome
        )

    def run_bqsr(
        self,
        input_bam: str,
        output_bam: str,
        reference_genome: str,
        known_sites: str,
    ) -> str:
        """Parabricks BQSR — recalibrage des scores de qualité (GPU)."""
        logger.info(f"Parabricks bqsr: {input_bam} -> {output_bam}")
        return self._run_parabricks_step(
            "bqsr",
            [input_bam],
            output_bam,
            reference_genome=reference_genome,
            extra_kwargs={"knownSites": known_sites},
        )

    def run_gatk_preprocessing(
        self,
        input_bam: str,
        output_bam: str,
        reference_genome: str,
    ) -> str:
        """MarkDuplicates + BQSR via Parabricks GPU."""
        from config.gatk_config import gatk_config

        current = input_bam
        if gatk_config.enable_mark_duplicates:
            dedup_bam = output_bam.replace(".bam", ".dedup.bam")
            current = self.run_markdup(current, dedup_bam, reference_genome)
        if gatk_config.enable_bqsr:
            current = self.run_bqsr(
                current,
                output_bam,
                reference_genome,
                gatk_config.known_sites_s3,
            )
        return current

    def cleanup(self) -> None:
        """Cleanup SSH connection, conteneurs Docker et instance."""
        self._destroy_parabricks_containers()

        if self.ssh_client:
            self.ssh_client.close()
            self.ssh_client = None
            logger.info("SSH connection closed")

