"""Agents métier : un agent = un outil du registre (src.orchestration.registry).

Les agents sont importés à la demande par le registre : aucun import ici, pour ne pas
charger torch, boto3 ou paramiko quand ils ne servent pas.
"""
