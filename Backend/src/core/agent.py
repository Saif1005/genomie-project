"""Common agent contract: one responsibility, one input (context), one output (AgentResult).

An agent knows neither the orchestrator nor the other agents: it reads the keys it needs from
the context and returns the keys it produces. The engine merges those outputs.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from loguru import logger


class AgentStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class AgentResult:
    success: bool
    status: AgentStatus
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    execution_time: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, **data: Any) -> "AgentResult":
        return cls(success=True, status=AgentStatus.COMPLETED, data=data)

    @classmethod
    def fail(cls, error: str, **data: Any) -> "AgentResult":
        return cls(success=False, status=AgentStatus.FAILED, error=error, data=data)


class AgentError(RuntimeError):
    """Expected domain error: message meant for the user, no stack trace."""


class BaseAgent(ABC):
    def __init__(self, agent_name: str, config: Optional[Dict[str, Any]] = None):
        self.agent_name = agent_name
        self.config = config or {}
        self.status = AgentStatus.IDLE
        self.result: Optional[AgentResult] = None
        self.logger = logger.bind(agent=agent_name)

    @abstractmethod
    def execute(self, context: Dict[str, Any]) -> AgentResult:
        """The agent's work; may raise AgentError for a clean domain failure."""

    def validate_input(self, context: Dict[str, Any]) -> bool:
        return True

    def run(self, context: Dict[str, Any]) -> AgentResult:
        start = time.perf_counter()
        self.status = AgentStatus.RUNNING
        try:
            if not self.validate_input(context):
                result = AgentResult.fail(f"{self.agent_name}: invalid or missing inputs")
            else:
                self.logger.info(f"{self.agent_name}: starting")
                result = self.execute(context)
        except AgentError as e:
            result = AgentResult.fail(str(e))
        except Exception as e:  # unexpected error: logged, never silent
            self.logger.exception(f"{self.agent_name} : exception")
            result = AgentResult.fail(f"{self.agent_name} : {type(e).__name__}: {e}")
        result.execution_time = time.perf_counter() - start
        self.status = result.status
        self.result = result
        if result.success:
            self.logger.info(f"{self.agent_name}: completed in {result.execution_time:.1f}s")
        else:
            self.logger.error(f"{self.agent_name}: failed — {result.error}")
        return result

    def get_status(self) -> Dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "status": self.status.value,
            "has_result": self.result is not None,
            "result_success": self.result.success if self.result else None,
            "execution_time": self.result.execution_time if self.result else None,
        }
