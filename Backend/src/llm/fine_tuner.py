"""Fine-tuning module for LLM on EC2 GPU instances."""

import os
import json
from pathlib import Path
from typing import Optional, Dict, List
from loguru import logger
import paramiko

from config.llm_config import llm_config
from config.aws_config import aws_config


class FineTuningError(Exception):
    """Custom exception for fine-tuning errors."""

    pass


class LLMFineTuner:
    """Fine-tune LLM on EC2 GPU instance using LoRA/QLoRA."""

    def __init__(
        self,
        instance_id: Optional[str] = None,
        ssh_key_path: Optional[str] = None,
        ssh_user: str = "ubuntu",
        model_name: Optional[str] = None,
    ):
        """
        Initialize LLM fine-tuner.

        Args:
            instance_id: Optional EC2 instance ID
            ssh_key_path: Path to SSH private key
            ssh_user: SSH username (default: ubuntu)
            model_name: Modèle BioLLM à fine-tuner (défaut: llm_config.model_name)
        """
        self.instance_id = instance_id
        self.ssh_key_path = ssh_key_path
        self.ssh_user = ssh_user
        self.model_name = model_name or llm_config.model_name
        # Imports AWS paresseux : boto3 n'est pas requis au démarrage de l'API en mode local
        from src.aws.ec2_manager import get_ec2_manager
        from src.aws.s3_manager import get_s3_manager

        self.ec2_manager = get_ec2_manager()
        self.s3_manager = get_s3_manager()
        self.ssh_client: Optional[paramiko.SSHClient] = None

    def _ensure_instance_running(self) -> str:
        """Ensure EC2 GPU instance is running."""
        if self.instance_id:
            info = self.ec2_manager.get_instance_info(self.instance_id)
            if info["state"] == "running":
                return self.instance_id
            elif info["state"] == "stopped":
                logger.info(f"Starting stopped instance: {self.instance_id}")
                self.ec2_manager.ec2_client.start_instances(
                    InstanceIds=[self.instance_id]
                )
                self.ec2_manager.wait_for_instance(self.instance_id, "running")
                return self.instance_id

        # Launch new GPU instance
        logger.info("Launching new EC2 GPU instance for fine-tuning")
        self.instance_id = self.ec2_manager.launch_instance(
            instance_type="p3.2xlarge",  # GPU required for training
            tags={"Name": "llm-finetuning", "Purpose": "llm-training"},
        )
        self.ec2_manager.wait_for_instance(self.instance_id, "running")
        return self.instance_id

    def _connect_ssh(self) -> paramiko.SSHClient:
        """Establish SSH connection to EC2 instance."""
        if self.ssh_client:
            return self.ssh_client

        if not self.ssh_key_path:
            raise FineTuningError("SSH key path is required")

        instance_info = self.ec2_manager.get_instance_info(self.instance_id)
        public_ip = instance_info.get("public_ip")

        if not public_ip:
            raise FineTuningError(f"Instance {self.instance_id} has no public IP")

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
            raise FineTuningError(error_msg) from e

    def _execute_remote_command(
        self, command: str, timeout: Optional[int] = None
    ) -> tuple[int, str, str]:
        """Execute command on remote instance via SSH."""
        ssh = self._connect_ssh()

        try:
            logger.debug(f"Executing remote command: {command}")
            stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout)

            exit_code = stdout.channel.recv_exit_status()
            stdout_text = stdout.read().decode("utf-8")
            stderr_text = stderr.read().decode("utf-8")

            if exit_code != 0:
                logger.warning(
                    f"Command failed with exit code {exit_code}: {stderr_text}"
                )

            return exit_code, stdout_text, stderr_text

        except Exception as e:
            error_msg = f"Failed to execute remote command: {e}"
            logger.error(error_msg)
            raise FineTuningError(error_msg) from e

    def setup_training_environment(self) -> None:
        """Setup training environment on EC2 instance."""
        logger.info("Setting up training environment on EC2")

        setup_script = """
        # Install Python and pip
        sudo apt-get update -y
        sudo apt-get install -y python3 python3-pip python3-venv git

        # Create virtual environment
        python3 -m venv ~/llm-env
        source ~/llm-env/bin/activate

        # Install PyTorch with CUDA support
        pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118

        # Install transformers and training libraries
        pip install transformers==4.36.2
        pip install peft==0.7.1
        pip install accelerate==0.25.0
        pip install bitsandbytes==0.41.3
        pip install datasets==2.16.1
        pip install trl==0.7.4
        pip install loguru

        # Create training directory
        mkdir -p ~/llm-training
        mkdir -p ~/llm-training/data
        mkdir -p ~/llm-training/models
        mkdir -p ~/llm-training/outputs
        """

        exit_code, stdout, stderr = self._execute_remote_command(setup_script)

        if exit_code != 0:
            raise FineTuningError(f"Failed to setup training environment: {stderr}")

        logger.info("Training environment setup completed")

    def upload_training_data(self, training_data_path: Path) -> str:
        """
        Upload training data to EC2 instance.

        Args:
            training_data_path: Local path to training data JSONL

        Returns:
            Remote path on EC2
        """
        logger.info(f"Uploading training data: {training_data_path}")

        # Upload to S3 first, then download on EC2
        s3_key = f"training-data/{training_data_path.name}"
        s3_uri = self.s3_manager.upload_file(
            str(training_data_path), s3_key, bucket_name=aws_config.s3_bucket_name
        )

        # Download on EC2
        remote_path = f"~/llm-training/data/{training_data_path.name}"
        command = f"aws s3 cp {s3_uri} {remote_path}"
        exit_code, stdout, stderr = self._execute_remote_command(command)

        if exit_code != 0:
            raise FineTuningError(f"Failed to download training data: {stderr}")

        logger.info(f"Training data uploaded to {remote_path}")
        return remote_path

    def create_training_script(
        self, training_data_path: str, output_dir: str
    ) -> str:
        """
        Create fine-tuning script for EC2.

        Args:
            training_data_path: Path to training data on EC2
            output_dir: Output directory for model

        Returns:
            Path to training script
        """
        script_content = """
import json
import os
from pathlib import Path
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    TrainingArguments,
    Trainer,
    DataCollatorForLanguageModeling,
)
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from datasets import load_dataset
from loguru import logger
import torch

# Configuration
model_name = "{model_name}"
training_data_path = "{training_data_path}"
output_dir = "{output_dir}"
epochs = {epochs}
batch_size = {batch_size}
learning_rate = {learning_rate}

logger.info(f"Starting fine-tuning: {{model_name}}")
logger.info(f"Training data: {{training_data_path}}")
logger.info(f"Output: {{output_dir}}")

# Load tokenizer
tokenizer = AutoTokenizer.from_pretrained(model_name)
if tokenizer.pad_token is None:
    tokenizer.pad_token = tokenizer.eos_token

# Load model with 4-bit quantization
model = AutoModelForCausalLM.from_pretrained(
    model_name,
    device_map="auto",
    load_in_4bit=True,
    torch_dtype=torch.float16,
)

# Prepare model for LoRA
model = prepare_model_for_kbit_training(model)

# Configure LoRA
lora_config = LoraConfig(
    r=16,
    lora_alpha=32,
    target_modules=["q_proj", "v_proj", "k_proj", "o_proj"],
    lora_dropout=0.05,
    bias="none",
    task_type="CAUSAL_LM",
)

model = get_peft_model(model, lora_config)

# Load dataset
def format_prompt(example):
    messages = example["messages"]
    text = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=False
    )
    return {{"text": text}}

dataset = load_dataset("json", data_files=training_data_path, split="train")
dataset = dataset.map(format_prompt, remove_columns=dataset.column_names)

# Tokenize
def tokenize(examples):
    return tokenizer(
        examples["text"],
        truncation=True,
        max_length={max_length},
        padding="max_length",
    )

tokenized_dataset = dataset.map(tokenize, batched=True)

# Training arguments
training_args = TrainingArguments(
    output_dir=output_dir,
    num_train_epochs=epochs,
    per_device_train_batch_size=batch_size,
    gradient_accumulation_steps=4,
    learning_rate=learning_rate,
    fp16=True,
    logging_steps={logging_steps},
    save_steps={save_steps},
    save_total_limit=3,
    optim="paged_adamw_8bit",
    warmup_steps=100,
)

# Data collator
data_collator = DataCollatorForLanguageModeling(
    tokenizer=tokenizer, mlm=False
)

# Trainer
trainer = Trainer(
    model=model,
    args=training_args,
    train_dataset=tokenized_dataset,
    data_collator=data_collator,
)

# Train
logger.info("Starting training...")
trainer.train()

# Save model
logger.info(f"Saving model to {{output_dir}}")
trainer.save_model()
tokenizer.save_pretrained(output_dir)

logger.info("Fine-tuning completed!")
"""

        script_content = script_content.format(
            model_name=self.model_name,
            training_data_path=training_data_path,
            output_dir=output_dir,
            epochs=llm_config.training_epochs,
            batch_size=llm_config.training_batch_size,
            learning_rate=llm_config.training_learning_rate,
            max_length=llm_config.max_length,
            logging_steps=llm_config.training_logging_steps,
            save_steps=llm_config.training_save_steps,
        )
        script_path = "~/llm-training/train_model.py"
        # Write script to file on EC2
        command = f'cat > {script_path} << "SCRIPTEOF"\n{script_content}\nSCRIPTEOF'
        exit_code, stdout, stderr = self._execute_remote_command(command)

        if exit_code != 0:
            raise FineTuningError(f"Failed to create training script: {stderr}")

        return script_path

    def run_fine_tuning(
        self,
        training_data_path: Path,
        output_dir: Optional[str] = None,
    ) -> str:
        """
        Run fine-tuning on EC2 instance.

        Args:
            training_data_path: Local path to training data
            output_dir: Optional output directory (defaults to config)

        Returns:
            Path to fine-tuned model
        """
        # Ensure instance is running
        self._ensure_instance_running()

        # Setup environment
        self.setup_training_environment()

        # Upload training data
        remote_data_path = self.upload_training_data(training_data_path)

        # Create output directory
        if output_dir is None:
            output_dir = f"~/llm-training/outputs/{self.model_name.split('/')[-1]}-ft"

        # Create training script
        script_path = self.create_training_script(remote_data_path, output_dir)

        # Run training
        logger.info("Starting fine-tuning on EC2...")
        command = f"source ~/llm-env/bin/activate && python {script_path}"
        exit_code, stdout, stderr = self._execute_remote_command(
            command, timeout=86400  # 24 hours
        )

        if exit_code != 0:
            raise FineTuningError(f"Fine-tuning failed: {stderr}")

        logger.info(f"Fine-tuning completed! Model saved to {output_dir}")

        # Upload model to S3
        s3_model_path = self._upload_model_to_s3(output_dir)

        return s3_model_path

    def _upload_model_to_s3(self, model_dir: str) -> str:
        """Upload fine-tuned model to S3."""
        logger.info(f"Uploading model to S3 from {model_dir}")

        s3_prefix = f"models/{self.model_name.split('/')[-1]}-ft"
        s3_uri = f"s3://{aws_config.s3_bucket_name}/{s3_prefix}"

        command = f"aws s3 sync {model_dir} {s3_uri}"
        exit_code, stdout, stderr = self._execute_remote_command(command)

        if exit_code != 0:
            raise FineTuningError(f"Failed to upload model to S3: {stderr}")

        logger.info(f"Model uploaded to {s3_uri}")
        return s3_uri

    def cleanup(self) -> None:
        """Cleanup SSH connection."""
        if self.ssh_client:
            self.ssh_client.close()
            self.ssh_client = None
            logger.info("SSH connection closed")

