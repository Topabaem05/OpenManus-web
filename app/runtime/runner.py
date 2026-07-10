"""In-memory TaskRunner that wraps a Manus agent with event emission.

The runner executes the agent's think/act loop, emitting structured
AgentEvents at each step. It supports cooperative cancellation via
CancellationToken. It does NOT change the agent's internal logic -
it hooks into the existing step() method and intercepts tool calls.
"""

import uuid
from typing import Optional

from pydantic import BaseModel

from app.logger import logger
from app.runtime.cancellation import CancellationToken, OperationCancelled
from app.runtime.events import (
    AgentEvent,
    ConsoleEventSink,
    EventSink,
    NullEventSink,
    TaskCompletedEvent,
    TaskCreatedEvent,
    TaskFailedEvent,
    TaskStatusChangedEvent,
    ToolCallOutputEvent,
    ToolCallStartedEvent,
    ModelRequestStartedEvent,
)
from app.runtime.workspace import TaskWorkspace
from app.schema import AgentState


class RunResult(BaseModel):
    """Result of a TaskRunner run."""

    task_id: str
    run_id: str
    status: str  # completed | cancelled | failed
    result: str = ""
    error: str = ""
    steps_executed: int = 0

    class Config:
        arbitrary_types_allowed = True


class TaskRunner:
    """Wraps a Manus agent, emitting events for each step.

    Args:
        agent: a Manus (or ToolCallAgent) instance.
        event_sink: where to emit events. Default: NullEventSink.
        task_id: optional task id. If None, generated.
    """

    def __init__(
        self,
        agent,
        event_sink: Optional[EventSink] = None,
        task_id: Optional[str] = None,
    ):
        self.agent = agent
        self.sink = event_sink or NullEventSink()
        self.task_id = task_id or f"task_{uuid.uuid4().hex[:12]}"
        self.run_id = f"run_{uuid.uuid4().hex[:12]}"
        self.workspace = TaskWorkspace(task_id=self.task_id)
        self._sequence = 0

    def _next_seq(self) -> int:
        self._sequence += 1
        return self._sequence

    async def _emit(self, event: AgentEvent) -> None:
        event.sequence = self._next_seq()
        await self.sink.emit(event)

    async def run(
        self,
        request: str,
        cancellation_token: Optional[CancellationToken] = None,
    ) -> RunResult:
        """Execute the agent with event emission and cancellation support.

        Args:
            request: the user's natural-language request.
            cancellation_token: optional token to request cancellation.

        Returns:
            RunResult with status, result text, and step count.
        """
        ct = cancellation_token or CancellationToken()
        self.workspace.create()

        await self._emit(
            TaskCreatedEvent(task_id=self.task_id, run_id=self.run_id, title=request[:100])
        )
        await self._emit(
            TaskStatusChangedEvent(
                task_id=self.task_id, run_id=self.run_id, status="running"
            )
        )

        if request:
            self.agent.update_memory("user", request)

        steps = 0
        try:
            async with self.agent.state_context(AgentState.RUNNING):
                while (
                    self.agent.current_step < self.agent.max_steps
                    and self.agent.state != AgentState.FINISHED
                ):
                    ct.check()

                    self.agent.current_step += 1
                    steps += 1
                    step_num = self.agent.current_step

                    await self._emit(
                        ModelRequestStartedEvent(
                            task_id=self.task_id,
                            run_id=self.run_id,
                            step=step_num,
                        )
                    )

                    # think() returns bool (whether to act)
                    should_act = await self.agent.think()
                    ct.check()

                    if not should_act:
                        break

                    # Intercept tool calls for event emission
                    if self.agent.tool_calls:
                        for tc in self.agent.tool_calls:
                            await self._emit(
                                ToolCallStartedEvent(
                                    task_id=self.task_id,
                                    run_id=self.run_id,
                                    tool=tc.function.name,
                                )
                            )

                    step_result = await self.agent.act()
                    ct.check()

                    # Emit tool output events from the last tool messages
                    if self.agent.tool_calls:
                        for tc in self.agent.tool_calls:
                            tool_msgs = [
                                m
                                for m in self.agent.memory.messages
                                if m.tool_call_id == tc.id
                            ]
                            output = tool_msgs[-1].content if tool_msgs else ""
                            await self._emit(
                                ToolCallOutputEvent(
                                    task_id=self.task_id,
                                    run_id=self.run_id,
                                    tool=tc.function.name,
                                    output=str(output)[:500],
                                )
                            )

                    if self.agent.is_stuck():
                        self.agent.handle_stuck_state()

            if self.agent.current_step >= self.agent.max_steps:
                status = "completed"
                result = f"Reached max steps ({self.agent.max_steps})"
            else:
                status = "completed"
                result = step_result or "Task completed"

            await self._emit(
                TaskStatusChangedEvent(
                    task_id=self.task_id, run_id=self.run_id, status=status
                )
            )
            await self._emit(
                TaskCompletedEvent(
                    task_id=self.task_id, run_id=self.run_id, result=result[:500]
                )
            )

            return RunResult(
                task_id=self.task_id,
                run_id=self.run_id,
                status=status,
                result=result,
                steps_executed=steps,
            )

        except OperationCancelled:
            await self._emit(
                TaskStatusChangedEvent(
                    task_id=self.task_id, run_id=self.run_id, status="cancelled"
                )
            )
            await self._emit(
                TaskCompletedEvent(
                    task_id=self.task_id,
                    run_id=self.run_id,
                    result="Task cancelled by user",
                )
            )
            return RunResult(
                task_id=self.task_id,
                run_id=self.run_id,
                status="cancelled",
                result="Cancelled",
                steps_executed=steps,
            )
        except Exception as e:
            logger.exception(f"TaskRunner error: {e}")
            await self._emit(
                TaskStatusChangedEvent(
                    task_id=self.task_id, run_id=self.run_id, status="failed"
                )
            )
            await self._emit(
                TaskFailedEvent(
                    task_id=self.task_id, run_id=self.run_id, error=str(e)
                )
            )
            return RunResult(
                task_id=self.task_id,
                run_id=self.run_id,
                status="failed",
                error=str(e),
                steps_executed=steps,
            )
        finally:
            try:
                await self.agent.cleanup()
            except Exception:
                pass
