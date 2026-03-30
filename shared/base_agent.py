from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentResult:
	agent_name: str
	protocol: str
	success: bool
	data: dict[str, Any] = field(default_factory=dict)
	error: str | None = None
	duration_seconds: float = 0.0


class BaseAgent(ABC):
	def __init__(self, agent_name: str, protocol: str) -> None:
		self.agent_name = agent_name
		self.protocol = protocol
		self.logger = logging.getLogger(self.agent_name)

	def run(self, **kwargs: Any) -> AgentResult:
		start = time.perf_counter()
		try:
			data = self.execute(**kwargs)
			return AgentResult(
				agent_name=self.agent_name,
				protocol=self.protocol,
				success=True,
				data=data,
				duration_seconds=round(time.perf_counter() - start, 4),
			)
		except Exception as exc:
			self.logger.exception("Agent execution failed")
			return AgentResult(
				agent_name=self.agent_name,
				protocol=self.protocol,
				success=False,
				error=str(exc),
				duration_seconds=round(time.perf_counter() - start, 4),
			)

	@abstractmethod
	def execute(self, **kwargs: Any) -> dict[str, Any]:
		raise NotImplementedError
