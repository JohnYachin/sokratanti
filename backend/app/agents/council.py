import asyncio
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from loguru import logger
from .base_agent import BaseAgent, AgentContext, AgentOutput, Signal
from .voting_engine import VotingEngine, VoteResult

class CouncilResult(BaseModel):
    cycle_id: str
    coin_id: str
    final_signal: str
    final_confidence: float
    consensus_score: float
    agent_outputs: List[AgentOutput]
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class AICouncil:
    def __init__(self, agents: List[BaseAgent], voting_engine: VotingEngine):
        self.agents = agents
        self.voting_engine = voting_engine
        self.min_quorum = 15

    async def run_cycle(self, context: AgentContext) -> CouncilResult:
        cycle_id = str(uuid.uuid4())
        logger.info(f"Starting council cycle {cycle_id} for {context.symbol} with {len(self.agents)} agents")
        
        # In a real app, implement Redis cache check here to prevent duplicate runs
        # active = await redis.get(f"cycle:{context.coin_id}")
        
        agent_outputs = await self._run_agents_parallel(context)
        
        valid_outputs = [out for out in agent_outputs if out.error is None]
        if len(valid_outputs) < self.min_quorum:
            logger.error(f"Quorum not met for cycle {cycle_id}. Required: {self.min_quorum}, Got: {len(valid_outputs)}")
            raise RuntimeError(f"Council quorum not met (got {len(valid_outputs)}/{len(self.agents)})")
            
        agents_meta = {a.agent_id: {"weight": a.weight, "name": a.name} for a in self.agents}
        vote_result = await self.voting_engine.compute_consensus(valid_outputs, agents_meta)
        
        result = CouncilResult(
            cycle_id=cycle_id,
            coin_id=context.coin_id,
            final_signal=vote_result.final_signal,
            final_confidence=vote_result.final_confidence,
            consensus_score=vote_result.consensus_score,
            agent_outputs=agent_outputs
        )
        
        await self._save_cycle(cycle_id, result)
        return result

    async def _run_agents_parallel(self, context: AgentContext) -> List[AgentOutput]:
        tasks = []
        for agent in self.agents:
            task = asyncio.create_task(
                self._run_with_timeout(agent, context)
            )
            tasks.append(task)
            
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        outputs = []
        for agent, res in zip(self.agents, results):
            if isinstance(res, Exception):
                logger.error(f"Agent {agent.name} failed with unhandled exception: {res}")
                outputs.append(AgentOutput(
                    agent_id=agent.agent_id,
                    agent_name=agent.name,
                    signal=Signal.HOLD,
                    confidence=0.0,
                    reasoning="Unhandled exception",
                    raw_response="",
                    execution_time_ms=0,
                    error=str(res)
                ))
            else:
                outputs.append(res)
                
        return outputs

    async def _run_with_timeout(self, agent: BaseAgent, context: AgentContext) -> AgentOutput:
        try:
            return await asyncio.wait_for(agent.analyze(context), timeout=agent.timeout)
        except asyncio.TimeoutError:
            logger.warning(f"Agent {agent.name} timed out after {agent.timeout}s")
            return AgentOutput(
                agent_id=agent.agent_id,
                agent_name=agent.name,
                signal=Signal.HOLD,
                confidence=0.0,
                reasoning="Timeout",
                raw_response="",
                execution_time_ms=int(agent.timeout * 1000),
                error="TimeoutError"
            )

    async def _save_cycle(self, cycle_id: str, result: CouncilResult) -> None:
        # Implement saving to Supabase here
        logger.info(f"Saving council result {cycle_id} to database.")
        pass
