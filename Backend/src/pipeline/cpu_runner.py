"""CPU runner module for executing CPU-native genomic pipeline (BWA-MEM + GATK) on EC2 instances.

This module provides a CPU-based alternative to Parabricks for instances without GPU.
It uses standard tools: BWA-MEM for alignment and GATK HaplotypeCaller for variant calling.
"""

from typing import Optional
from pathlib import Path
from loguru import logger
import paramiko
import shlex
import os
import re
import shutil

from config.aws_config import aws_config
from config.deployment import is_local
from config.gatk_config import gatk_config
from config.runtime_config import runtime_config
from src.pipeline.exec_utils import is_local_ec2_instance, execute_command


class CPURunnerError(Exception):
    """Custom exception for CPU runner execution errors."""
    pass


class CPURunner:
    """Runner for executing CPU-native genomic pipeline (BWA + GATK) on EC2 instances."""

    def __init__(
        self,
        instance_id: Optional[str] = None,
        ssh_key_path: Optional[str] = None,
        ssh_user: str = "ubuntu",
    ):
        """
        Initialize CPU runner.

        Args:
            instance_id: EC2 instance ID (optional, will be auto-detected)
            ssh_key_path: Path to SSH private key
            ssh_user: SSH username (default: ubuntu)
        """
        self.instance_id = instance_id
        self.ssh_key_path = ssh_key_path
        self.ssh_user = ssh_user
        self.ssh_client: Optional[paramiko.SSHClient] = None
        self.local_mode = is_local_ec2_instance(instance_id)
        # Mode local : répertoire sous LOCAL_DATA_ROOT, monté au même chemin sur l'hôte,
        # pour que les conteneurs GATK (docker run -v) voient les mêmes fichiers.
        self.work_dir = (
            str(runtime_config.work_mount / "cpu_pipeline")
            if is_local()
            else "/tmp/genomic_pipeline"
        )
        if self.local_mode:
            logger.info("CPURunner: exécution locale (sans SSH)")
            if not self.instance_id:
                from src.pipeline.exec_utils import get_metadata_instance_id
                self.instance_id = get_metadata_instance_id()
        else:
            from src.aws.ec2_manager import get_ec2_manager  # boto3 : mode aws uniquement

            self.ec2_manager = get_ec2_manager()

    def _connect_ssh(self) -> None:
        """Establish SSH connection to EC2 instance (skip if local)."""
        if self.local_mode:
            return
        if self.ssh_client and self.ssh_client.get_transport() and self.ssh_client.get_transport().is_active():
            return  # Already connected

        if not self.instance_id:
            # Try to get instance ID from metadata (if running on EC2)
            try:
                import urllib.request
                self.instance_id = urllib.request.urlopen(
                    "http://169.254.169.254/latest/meta-data/instance-id",
                    timeout=2
                ).read().decode("utf-8")
            except Exception:
                raise CPURunnerError("Instance ID not provided and could not be auto-detected")

        if not self.ssh_key_path:
            # Try to find SSH key
            default_key = os.path.expanduser("~/.ssh/saif-pipeline-complet")
            if os.path.exists(default_key):
                self.ssh_key_path = default_key
            else:
                raise CPURunnerError(
                    "SSH key path not provided and default not found.\n"
                    f"Expected key file: {default_key}\n"
                    "Please provide the SSH key path using --ssh-key argument, or:\n"
                    "1. Download the key pair from AWS EC2 Console\n"
                    "2. Save it to ~/.ssh/saif-pipeline-complet\n"
                    "3. Set permissions: chmod 400 ~/.ssh/saif-pipeline-complet"
                )
        else:
            # Expand the SSH path provided (resolve ~)
            self.ssh_key_path = os.path.expanduser(self.ssh_key_path)
        
        # Verify SSH key file exists
        if not os.path.exists(self.ssh_key_path):
            raise CPURunnerError(
                f"SSH key file not found: {self.ssh_key_path}\n"
                "Please check the file path and ensure:\n"
                "1. The key file exists at the specified location\n"
                "2. You have read permissions for the file\n"
                "3. The key file has correct permissions (chmod 400)"
            )
        
        # Verify SSH key file permissions
        import stat
        key_stat = os.stat(self.ssh_key_path)
        key_perms = stat.filemode(key_stat.st_mode)
        if key_perms not in ['-r--------', '-rw-------', '-r--r--r--']:
            logger.warning(
                f"SSH key file has insecure permissions: {key_perms}\n"
                f"Recommended: chmod 400 {self.ssh_key_path}"
            )

        instance_info = self.ec2_manager.get_instance_info(self.instance_id)
        public_ip = instance_info.get("public_ip")

        if not public_ip:
            raise CPURunnerError(f"Instance {self.instance_id} has no public IP")

        try:
            logger.info(f"Connecting to {public_ip} via SSH using key: {self.ssh_key_path}")
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

        except Exception as e:
            error_msg = f"Failed to connect via SSH: {e}"
            logger.error(error_msg)
            raise CPURunnerError(error_msg) from e

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

        self._connect_ssh()

        try:
            logger.debug(f"Executing remote command: {command[:200]}...")
            stdin, stdout, stderr = self.ssh_client.exec_command(command, timeout=timeout)

            exit_code = stdout.channel.recv_exit_status()
            stdout_text = stdout.read().decode("utf-8")
            stderr_text = stderr.read().decode("utf-8")

            if exit_code != 0:
                logger.warning(f"Command failed with exit code {exit_code}")
                if stderr_text:
                    logger.warning(f"Stderr: {stderr_text[:500]}")
                if stdout_text:
                    logger.debug(f"Stdout: {stdout_text[:500]}")

            return exit_code, stdout_text, stderr_text

        except Exception as e:
            error_msg = f"Failed to execute remote command: {e}"
            logger.error(error_msg)
            raise CPURunnerError(error_msg) from e

    def _install_tools(self) -> None:
        """Install BWA, samtools, and GATK on the EC2 instance if not already installed."""
        logger.info("Checking for BWA, samtools, and GATK...")

        # Check if tools are installed
        check_cmd = "which bwa && which samtools || echo 'missing'"
        exit_code, stdout, stderr = self._execute_remote_command(check_cmd)

        if "missing" not in stdout:
            logger.info("✓ BWA and samtools are already installed")
        else:
            logger.info("Installing BWA and samtools...")

            # Install via apt (most reliable on Ubuntu)
            install_cmd = """
            set -e
            SUDO=$(command -v sudo || true)
            $SUDO apt-get update -qq
            $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y bwa samtools
            """

            exit_code, stdout, stderr = self._execute_remote_command(install_cmd, timeout=600)

            if exit_code != 0:
                logger.warning(f"apt-get installation failed: {stderr}")
                # Try alternative: install from source or conda
                logger.info("Trying alternative installation methods...")
                alt_install_cmd = """
                if command -v conda &> /dev/null; then
                    conda install -y -c bioconda bwa samtools || true
                fi
                """
                exit_code, stdout, stderr = self._execute_remote_command(alt_install_cmd, timeout=600)

            # Verify installation
            verify_cmd = "which bwa && which samtools && bwa && samtools --version"
            exit_code, stdout, stderr = self._execute_remote_command(verify_cmd)

            if exit_code == 0:
                logger.info("✓ BWA and samtools installed successfully")
            else:
                raise CPURunnerError(f"Failed to install BWA/samtools: {stderr}")

        # Check for GATK (via Docker)
        check_gatk_cmd = "docker images | grep -q gatk || echo 'missing'"
        exit_code, stdout, stderr = self._execute_remote_command(check_gatk_cmd)

        if "missing" not in stdout:
            logger.info("✓ GATK Docker image is available")
        else:
            logger.info("Pulling GATK Docker image...")
            pull_cmd = "docker pull broadinstitute/gatk:4.2.6.1"
            exit_code, stdout, stderr = self._execute_remote_command(pull_cmd, timeout=1800)

            if exit_code != 0:
                logger.warning(f"GATK Docker pull failed: {stderr}")
                # Try latest version
                pull_cmd = "docker pull broadinstitute/gatk:latest"
                exit_code, stdout, stderr = self._execute_remote_command(pull_cmd, timeout=1800)

            if exit_code == 0:
                logger.info("✓ GATK Docker image pulled successfully")
            else:
                logger.warning("GATK Docker image not available, will try to use system GATK if available")

    @staticmethod
    def _stage_local(src: str, dest: str) -> bool:
        """Place un fichier local dans work_dir (lien physique, sinon copie). False si absent."""
        src_p, dest_p = Path(src), Path(dest)
        if not src_p.is_file():
            return False
        dest_p.parent.mkdir(parents=True, exist_ok=True)
        if dest_p.exists() or dest_p.is_symlink():
            dest_p.unlink()
        try:
            os.link(src_p, dest_p)
        except OSError:
            shutil.copy2(src_p, dest_p)
        return True

    def _stage_reference(self, reference_genome: str, ref_local: str) -> None:
        """Référence locale + .fai + .dict dans work_dir (GATK exige reference.dict)."""
        if not self._stage_local(reference_genome, ref_local):
            raise CPURunnerError(f"Référence introuvable: {reference_genome}")
        self._stage_local(f"{reference_genome}.fai", f"{ref_local}.fai")
        src_dict = str(Path(reference_genome).with_suffix(".dict"))
        self._stage_local(src_dict, str(Path(ref_local).with_suffix(".dict")))

    def _publish_local(self, local_file: str, output_path: str) -> str:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_file, out)
        for ext in (".bai", ".tbi"):
            if Path(f"{local_file}{ext}").exists():
                shutil.copy2(f"{local_file}{ext}", f"{out}{ext}")
        return str(out)

    def _download_from_s3(self, s3_path: str, local_path: str) -> None:
        """Download file from S3 to local path on EC2 instance."""
        logger.info(f"Downloading {s3_path} to {local_path}...")
        
        # Extract region from S3 path or use default
        region = aws_config.region
        
        cmd = f"aws s3 cp {shlex.quote(s3_path)} {shlex.quote(local_path)} --region {region}"
        exit_code, stdout, stderr = self._execute_remote_command(cmd, timeout=3600)

        if exit_code != 0:
            raise CPURunnerError(f"Failed to download from S3: {stderr}")

        logger.info(f"✓ Downloaded: {local_path}")

    def _upload_to_s3(self, local_path: str, s3_path: str) -> None:
        """Upload file from local path on EC2 instance to S3."""
        logger.info(f"Uploading {local_path} to {s3_path}...")
        
        region = aws_config.region
        
        cmd = f"aws s3 cp {shlex.quote(local_path)} {shlex.quote(s3_path)} --region {region}"
        exit_code, stdout, stderr = self._execute_remote_command(cmd, timeout=3600)

        if exit_code != 0:
            raise CPURunnerError(f"Failed to upload to S3: {stderr}")

        logger.info(f"✓ Uploaded: {s3_path}")

    def run_fq2bam(
        self,
        fastq_r1: str,
        fastq_r2: str,
        output_bam: str,
        reference_genome: str,
    ) -> str:
        """
        Run BWA-MEM alignment (FASTQ to BAM).

        Args:
            fastq_r1: Path to R1 FASTQ file (local or S3 URI)
            fastq_r2: Path to R2 FASTQ file (local or S3 URI)
            output_bam: Output BAM file path (local or S3 URI)
            reference_genome: Reference genome path (local or S3 URI)

        Returns:
            Path to output BAM file
        """
        self._connect_ssh()
        self._install_tools()

        logger.info("Starting CPU-native BWA-MEM alignment pipeline")
        logger.info(f"Input R1: {fastq_r1}")
        logger.info(f"Input R2: {fastq_r2}")
        logger.info(f"Output BAM: {output_bam}")
        logger.info(f"Reference: {reference_genome}")

        # Create work directory
        work_dir = self.work_dir
        self._execute_remote_command(f"mkdir -p {work_dir}")

        # Download files from S3 if needed
        r1_local = f"{work_dir}/R1.fastq.gz"
        r2_local = f"{work_dir}/R2.fastq.gz"
        ref_local = f"{work_dir}/reference.fa"
        bam_local = f"{work_dir}/aligned.sorted.bam"

        if fastq_r1.startswith("s3://"):
            self._download_from_s3(fastq_r1, r1_local)
        elif Path(fastq_r1).is_file():
            r1_local = str(Path(fastq_r1).resolve())
        else:
            raise CPURunnerError(f"FASTQ R1 introuvable: {fastq_r1}")

        if fastq_r2.startswith("s3://"):
            self._download_from_s3(fastq_r2, r2_local)
        elif Path(fastq_r2).is_file():
            r2_local = str(Path(fastq_r2).resolve())
        else:
            raise CPURunnerError(f"FASTQ R2 introuvable: {fastq_r2}")

        if reference_genome.startswith("s3://"):
            self._download_from_s3(reference_genome, ref_local)
        elif Path(reference_genome).is_file():
            ref_local = str(Path(reference_genome).resolve())
        else:
            raise CPURunnerError(f"Référence introuvable: {reference_genome}")

        # Index reference genome if needed
        logger.info("Indexing reference genome...")
        index_check_cmd = f"test -f {ref_local}.bwt || echo 'missing'"
        exit_code, stdout, stderr = self._execute_remote_command(index_check_cmd)
        
        if "missing" in stdout:
            index_cmd = f"cd {work_dir} && bwa index {ref_local}"
            exit_code, stdout, stderr = self._execute_remote_command(index_cmd, timeout=3600)
            if exit_code != 0:
                raise CPURunnerError(f"BWA index failed: {stderr}")

        # Extract patient ID from FASTQ path or use default
        patient_id = "PATIENT001"
        if "patients/" in fastq_r1:
            try:
                patient_id = fastq_r1.split("patients/")[1].split("/")[0]
            except IndexError:
                pass

        # Run BWA-MEM with Read Groups (essential for GATK)
        logger.info("Running BWA-MEM alignment (this may take a while on CPU)...")
        sam_output = f"{work_dir}/aligned.sam"
        
        read_group = f"@RG\\tID:{patient_id}\\tSM:{patient_id}\\tPL:ILLUMINA\\tLB:lib1\\tPU:unit1"
        
        bwa_cmd = (
            f"cd {work_dir} && "
            f"bwa mem -t $(nproc) -R {shlex.quote(read_group)} "
            f"{shlex.quote(ref_local)} {shlex.quote(r1_local)} {shlex.quote(r2_local)} > {shlex.quote(sam_output)}"
        )

        exit_code, stdout, stderr = self._execute_remote_command(bwa_cmd, timeout=86400)  # 24 hours

        if exit_code != 0:
            raise CPURunnerError(f"BWA-MEM failed: {stderr}")

        # Convert SAM to BAM and sort
        logger.info("Converting SAM to sorted BAM...")
        samtools_cmd = (
            f"cd {work_dir} && "
            f"samtools view -bS {shlex.quote(sam_output)} | "
            f"samtools sort -o {shlex.quote(bam_local)} -"
        )

        exit_code, stdout, stderr = self._execute_remote_command(samtools_cmd, timeout=3600)

        if exit_code != 0:
            raise CPURunnerError(f"samtools sort failed: {stderr}")

        # Index BAM
        logger.info("Indexing BAM file...")
        index_cmd = f"samtools index {bam_local}"
        exit_code, stdout, stderr = self._execute_remote_command(index_cmd, timeout=600)

        if exit_code != 0:
            raise CPURunnerError(f"samtools index failed: {stderr}")

        # Upload to S3 or copie vers chemin local
        if output_bam.startswith("s3://"):
            self._upload_to_s3(bam_local, output_bam)
        else:
            out = Path(output_bam)
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bam_local, out)
            output_bam = str(out)

        # Cleanup local files
        cleanup_cmd = f"rm -f {work_dir}/*.sam {work_dir}/*.fastq.gz {work_dir}/reference.fa*"
        self._execute_remote_command(cleanup_cmd)

        logger.info(f"BWA-MEM alignment completed: {output_bam}")
        return output_bam

    def run_gatk_preprocessing(
        self,
        input_bam: str,
        output_bam: str,
        reference_genome: str,
    ) -> str:
        """
        GATK Best Practices: MarkDuplicates puis BaseRecalibrator + ApplyBQSR.

        Args:
            input_bam: BAM aligné (S3 URI)
            output_bam: BAM recalibré (S3 URI)
            reference_genome: Référence FASTA (S3 URI)

        Returns:
            Chemin S3 du BAM recalibré
        """
        if not gatk_config.enable_mark_duplicates and not gatk_config.enable_bqsr:
            logger.info("Prétraitement GATK désactivé — BAM inchangé")
            return input_bam

        self._connect_ssh()
        self._install_tools()
        work_dir = self.work_dir
        self._execute_remote_command(f"mkdir -p {work_dir}")

        bam_local = f"{work_dir}/input.bam"
        dedup_local = f"{work_dir}/dedup.bam"
        recal_local = f"{work_dir}/recal.bam"
        ref_local = f"{work_dir}/reference.fa"
        metrics_local = f"{work_dir}/dup_metrics.txt"
        table_local = f"{work_dir}/recal_data.table"

        if input_bam.startswith("s3://"):
            self._download_from_s3(input_bam, bam_local)
        elif self.local_mode and self._stage_local(input_bam, bam_local):
            pass
        else:
            raise CPURunnerError(f"input_bam introuvable: {input_bam}")
        self._execute_remote_command(f"rm -f {bam_local}.bai")
        self._execute_remote_command(f"samtools index {bam_local}")

        if reference_genome.startswith("s3://"):
            self._download_from_s3(reference_genome, ref_local)
            try:
                self._download_from_s3(f"{reference_genome}.fai", f"{ref_local}.fai")
            except Exception:
                self._execute_remote_command(f"samtools faidx {ref_local}")
        elif self.local_mode:
            self._stage_reference(reference_genome, ref_local)
            if not Path(f"{ref_local}.fai").exists():
                self._execute_remote_command(f"samtools faidx {ref_local}")
        else:
            raise CPURunnerError("reference_genome doit être une URI S3")

        current_bam = bam_local
        gatk_img = gatk_config.docker_image

        if gatk_config.enable_mark_duplicates:
            logger.info("GATK MarkDuplicates...")
            md_cmd = (
                f"docker run --rm -v {work_dir}:/data {gatk_img} "
                f"gatk MarkDuplicates -I /data/input.bam -O /data/dedup.bam "
                f"-M /data/dup_metrics.txt --CREATE_INDEX true"
            )
            exit_code, _, stderr = self._execute_remote_command(md_cmd, timeout=14400)
            if exit_code != 0:
                raise CPURunnerError(f"MarkDuplicates failed: {stderr}")
            current_bam = dedup_local

        if gatk_config.enable_bqsr:
            known = gatk_config.known_sites_s3
            mills = gatk_config.mills_indels_s3
            known_local = f"{work_dir}/known_sites.vcf.gz"
            mills_local = f"{work_dir}/mills.vcf.gz"
            for s3_uri, local in ((known, known_local), (mills, mills_local)):
                if not s3_uri.startswith("s3://"):
                    if self._stage_local(s3_uri, local):
                        self._stage_local(f"{s3_uri}.tbi", f"{local}.tbi")
                    else:
                        logger.warning(f"Sites connus indisponibles ({s3_uri}) — BQSR sans ce fichier")
                    continue
                try:
                    self._download_from_s3(s3_uri, local)
                except Exception as e:
                    logger.warning(f"Sites connus indisponibles ({s3_uri}): {e} — BQSR sans ce fichier")

            bam_name = "dedup.bam" if gatk_config.enable_mark_duplicates else "input.bam"
            logger.info("GATK BaseRecalibrator + ApplyBQSR...")
            br_cmd = (
                f"docker run --rm -v {work_dir}:/data {gatk_img} "
                f"gatk BaseRecalibrator -R /data/reference.fa -I /data/{bam_name} "
                f"--known-sites /data/known_sites.vcf.gz "
                f"-O /data/recal_data.table"
            )
            exit_code, _, stderr = self._execute_remote_command(br_cmd, timeout=14400)
            if exit_code != 0:
                logger.warning(f"BaseRecalibrator partiel: {stderr} — copie BAM sans BQSR")
                self._execute_remote_command(f"cp /data/{bam_name} /data/recal.bam")
            else:
                ar_cmd = (
                    f"docker run --rm -v {work_dir}:/data {gatk_img} "
                    f"gatk ApplyBQSR -R /data/reference.fa -I /data/{bam_name} "
                    f"--bqsr-recal-file /data/recal_data.table -O /data/recal.bam"
                )
                exit_code, _, stderr = self._execute_remote_command(ar_cmd, timeout=14400)
                if exit_code != 0:
                    raise CPURunnerError(f"ApplyBQSR failed: {stderr}")
            current_bam = recal_local
            self._execute_remote_command(f"samtools index {current_bam}")

        if output_bam.startswith("s3://"):
            self._upload_to_s3(current_bam, output_bam)
        elif self.local_mode:
            output_bam = self._publish_local(current_bam, output_bam)
        else:
            raise CPURunnerError("output_bam doit être une URI S3")
        logger.info(f"Prétraitement GATK terminé: {output_bam}")
        return output_bam

    def run_haplotypecaller(
        self,
        input_bam: str,
        output_vcf: str,
        reference_genome: str,
    ) -> str:
        """
        Run GATK HaplotypeCaller (BAM to VCF).

        Args:
            input_bam: Input BAM file path (local or S3 URI)
            output_vcf: Output VCF file path (local or S3 URI)
            reference_genome: Reference genome path (local or S3 URI)

        Returns:
            Path to output VCF file
        """
        self._connect_ssh()
        self._install_tools()

        logger.info("Starting GATK HaplotypeCaller")
        logger.info(f"Input BAM: {input_bam}")
        logger.info(f"Output VCF: {output_vcf}")
        logger.info(f"Reference: {reference_genome}")

        # Create work directory
        work_dir = self.work_dir
        self._execute_remote_command(f"mkdir -p {work_dir}")

        # Download files from S3
        bam_local = f"{work_dir}/input.bam"
        bam_index_local = f"{work_dir}/input.bam.bai"
        ref_local = f"{work_dir}/reference.fa"
        ref_index_local = f"{work_dir}/reference.fa.fai"
        vcf_local = f"{work_dir}/variants.vcf.gz"

        if input_bam.startswith("s3://"):
            self._download_from_s3(input_bam, bam_local)
            # Remove any existing BAM index so we always recreate it: a stale .bai
            # (e.g. from S3) can be older than the BAM and cause "Invalid GZIP header".
            self._execute_remote_command(f"rm -f {bam_index_local}")
        elif self.local_mode and self._stage_local(input_bam, bam_local):
            self._execute_remote_command(f"rm -f {bam_index_local}")
        else:
            raise CPURunnerError(f"BAM introuvable: {input_bam}")

        if reference_genome.startswith("s3://"):
            self._download_from_s3(reference_genome, ref_local)
            # Try to download index
            try:
                self._download_from_s3(f"{reference_genome}.fai", ref_index_local)
            except:
                logger.info("Reference index not found, will be created")
        elif self.local_mode:
            self._stage_reference(reference_genome, ref_local)
        else:
            raise CPURunnerError("Local reference genome not supported, use S3 URI")

        # Create reference index if needed
        index_check_cmd = f"test -f {ref_index_local} || echo 'missing'"
        exit_code, stdout, stderr = self._execute_remote_command(index_check_cmd)
        
        if "missing" in stdout:
            logger.info("Creating reference index...")
            index_cmd = f"samtools faidx {ref_local}"
            exit_code, stdout, stderr = self._execute_remote_command(index_cmd, timeout=600)
            if exit_code != 0:
                raise CPURunnerError(f"Reference indexing failed: {stderr}")

        # Create BAM index if needed
        index_check_cmd = f"test -f {bam_index_local} || echo 'missing'"
        exit_code, stdout, stderr = self._execute_remote_command(index_check_cmd)
        
        if "missing" in stdout:
            logger.info("Creating BAM index...")
            index_cmd = f"samtools index {bam_local}"
            exit_code, stdout, stderr = self._execute_remote_command(index_cmd, timeout=600)
            if exit_code != 0:
                raise CPURunnerError(f"BAM indexing failed: {stderr}")

        # Run GATK HaplotypeCaller via Docker
        logger.info("Running GATK HaplotypeCaller (this may take several hours on CPU)...")
        
        gatk_cmd = (
            f"docker run --rm -v {work_dir}:/data "
            f"broadinstitute/gatk:4.2.6.1 "
            f"gatk HaplotypeCaller "
            f"-R /data/reference.fa "
            f"-I /data/input.bam "
            f"-O /data/variants.vcf.gz"
        )

        exit_code, stdout, stderr = self._execute_remote_command(gatk_cmd, timeout=43200)  # 12 hours

        if exit_code != 0:
            raise CPURunnerError(f"GATK HaplotypeCaller failed: {stderr}")

        # Upload to S3 (ou copie vers patients/<ID>/output en mode local)
        if output_vcf.startswith("s3://"):
            self._upload_to_s3(vcf_local, output_vcf)
        elif self.local_mode:
            output_vcf = self._publish_local(vcf_local, output_vcf)
        else:
            raise CPURunnerError("Local output VCF not supported, use S3 URI")

        # Cleanup local files
        cleanup_cmd = f"rm -f {work_dir}/input.bam* {work_dir}/reference.fa* {work_dir}/variants.vcf.gz"
        self._execute_remote_command(cleanup_cmd)

        logger.info(f"GATK HaplotypeCaller completed: {output_vcf}")
        return output_vcf

    def cleanup(self) -> None:
        """Cleanup SSH connection."""
        if self.ssh_client:
            self.ssh_client.close()
            self.ssh_client = None
            logger.info("SSH connection closed")
