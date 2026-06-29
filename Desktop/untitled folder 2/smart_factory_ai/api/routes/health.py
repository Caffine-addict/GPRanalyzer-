from fastapi import APIRouter

router = APIRouter()


@router.get("/")
def root():

    return {

        "application": "Smart Factory AI",

        "status": "running"

    }


@router.get("/health")
def health():

    return {

        "status": "healthy"

    }