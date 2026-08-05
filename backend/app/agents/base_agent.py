import abc
import asyncio
import json
import time
from enum import Enum
from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, Field
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

class Signal(str, Enum):
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"

class AgentContext(BaseModel):
    coin_id: str
    symbol: str
    price: float
    market_cap: float
    volume_24h: float
    price_change_24h: float
    technical_indicators: Dict[str, Any]
    recent_news: List[str]
    on_chain_metrics: Dict[str, Any]
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class AgentOutput(BaseModel):
    agent_id: str
    agent_name: str
    signal: Signal
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    raw_response: str
    execution_time_ms: int
    error: Optional[str] = None

class Memory(BaseModel):
    content: str
    importance_score: float
    created_at: datetime = Field(default_factory=datetime.utcnow)

class BaseAgent(abc.ABC):
    agent_id: str
    name: str  
    specialization: str
    model: str = "gpt-4o"
    weight: float = 1.0
    timeout: float = 30.0

    async def analyze(self, context: AgentContext) -> AgentOutput:
        start_time = time.time()
        logger.info(f"Agent {self.name} ({self.agent_id}) starting analysis for {context.symbol}")
        
        try:
            # 1. Get memories
            memories = await self._get_memories(context.symbol)
            memory_context = "\n".join([m.content for m in memories])
            
            # 2. Build prompts
            system_prompt = self.get_system_prompt()
            user_prompt = self.build_user_prompt(context)
            if memory_context:
                user_prompt += f"\n\nRelevant past memories:\n{memory_context}"

            # 3. Call LLM
            raw_response = await self._call_llm_with_retry(system_prompt, user_prompt)
            
            # 4. Parse output
            parsed = await self._parse_output(raw_response)
            
            execution_time_ms = int((time.time() - start_time) * 1000)
            
            output = AgentOutput(
                agent_id=self.agent_id,
                agent_name=self.name,
                signal=parsed.get("signal", Signal.HOLD),
                confidence=parsed.get("confidence", 0.0),
                reasoning=parsed.get("reasoning", "No reasoning provided."),
                raw_response=raw_response,
                execution_time_ms=execution_time_ms
            )
            
            # 5. Save memory
            await self._save_memory(f"Analyzed {context.symbol}: {output.signal} ({output.confidence})", output)
            
            logger.info(f"Agent {self.name} finished analysis in {execution_time_ms}ms")
            return output
            
        except Exception as e:
            execution_time_ms = int((time.time() - start_time) * 1000)
            logger.error(f"Agent {self.name} failed during analysis: {e}")
            return AgentOutput(
                agent_id=self.agent_id,
                agent_name=self.name,
                signal=Signal.HOLD,
                confidence=0.0,
                reasoning="Error occurred during analysis.",
                raw_response="",
                execution_time_ms=execution_time_ms,
                error=str(e)
            )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def _call_llm_with_retry(self, system_prompt: str, user_prompt: str) -> str:
        return await self._call_llm(f"{system_prompt}\n{user_prompt}")

    async def _call_llm(self, prompt: str) -> str:
        # Mocking LLM call for implementation. Integrate with OpenAI API here.
        await asyncio.sleep(1) # simulate network latency
        return json.dumps({
            "signal": "HOLD", 
            "confidence": 0.5, 
            "reasoning": "Mocked response"
        })

    async def _parse_output(self, raw: str) -> AgentOutput:
        try:
            clean = raw.strip()
            if clean.startswith("```json"):
                clean = clean[7:-3]
            elif clean.startswith("```"):
                clean = clean[3:-3]
            return json.loads(clean.strip())
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse LLM output: {raw}")
            raise ValueError("Invalid JSON output from LLM") from e

    async def _get_memories(self, query: str, limit: int = 5) -> List[Memory]:
        # Implement memory retrieval here
        return []

    async def _save_memory(self, content: str, output: AgentOutput) -> None:
        # Implement memory saving here
        pass

    @abc.abstractmethod
    def get_system_prompt(self) -> str:
        pass

    @abc.abstractmethod
    def build_user_prompt(self, context: AgentContext) -> str:
        pass
