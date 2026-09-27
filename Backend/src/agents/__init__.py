"""Domain agents: one agent = one tool of the registry (src.orchestration.registry).

Agents are imported on demand by the registry: no imports here, so that torch, boto3 or
paramiko are not loaded when unused.
"""
