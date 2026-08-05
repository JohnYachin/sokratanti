from fastapi import APIRouter, Depends, HTTPException
from typing import List, Optional
from pydantic import BaseModel

router = APIRouter(prefix="/agents", tags=["agents"])

class AgentStats(BaseModel):
    id: str
    name: str
    role: str
    success_rate: float
    total_votes: int

class AgentDetail(AgentStats):
    recent_votes: List[dict]
    performance_metrics: dict

class InvokeCouncilRequest(BaseModel):
    coin_id: str

@router.get("", response_model=List[AgentStats])
async def list_agents():
    return []

@router.get("/{id}", response_model=AgentDetail)
async def get_agent_detail(id: str):
    raise HTTPException(status_code=404, detail="Agent not found")

@router.get("/{id}/history")
async def get_agent_history(id: str):
    return []

@router.post("/invoke")
async def invoke_council(request: InvokeCouncilRequest):
    return {"status": "started", "coin_id": request.coin_id}
