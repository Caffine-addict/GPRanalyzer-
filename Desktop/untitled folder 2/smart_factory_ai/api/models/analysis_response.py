from pydantic import BaseModel


class AlertResponse(BaseModel):
    severity: str
    category: str
    message: str


class RecommendationResponse(BaseModel):
    priority: str
    recommendations: list[str]


class HealthResponse(BaseModel):
    score: int
    health: str


class RiskResponse(BaseModel):
    risk_score: int
    risk_level: str
    health_score: int
    reasons: list[str]


class AnalysisResponse(BaseModel):
    board_name: str
    board_family: str
    program_name: str

    health: HealthResponse
    risk: RiskResponse

    recommendation: RecommendationResponse

    alerts: list[AlertResponse]