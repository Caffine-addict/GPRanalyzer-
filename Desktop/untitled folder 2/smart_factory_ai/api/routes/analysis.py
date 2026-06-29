from fastapi import APIRouter

from machine_types.ict.event_repository import ICTEventRepository

from analytics.context.analysis_service import AnalysisService

router = APIRouter(
    prefix="/analysis",
    tags=["Analysis"]
)


@router.get("/latest")
def latest_analysis():

    repo = ICTEventRepository()

    service = AnalysisService()

    event = repo.latest()

    context = service.build(event)

    return {

        "board_name": event.board_name,

        "board_family": event.board_family,

        "program_name": event.program_name,

        "health": context.health,

        "risk": context.risk,

        "recommendation": context.recommendation,

        "alerts": context.alerts

    }