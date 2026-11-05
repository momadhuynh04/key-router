from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from proxy import testlab

router = APIRouter()


class TestImage(BaseModel):
    media_type: str
    data_base64: str


class TestRunRequest(BaseModel):
    cli: str = "claude"
    model: str = ""
    workdir: str
    timeout_sec: Optional[int] = None
    scenario: str = "quick-check"
    image: Optional[TestImage] = None


def _run(run_id: str) -> testlab.TestRun:
    run = testlab.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Test run '{run_id}' not found")
    return run


@router.get("/api/testlab/meta")
async def testlab_meta():
    return testlab.meta()


@router.post("/api/testlab/runs", status_code=202)
async def create_test_run(request: TestRunRequest):
    try:
        run = testlab.start_run(
            cli=request.cli,
            model=request.model,
            workdir=request.workdir,
            timeout_sec=request.timeout_sec,
            scenario=request.scenario,
            image_media_type=request.image.media_type if request.image else None,
            image_base64=request.image.data_base64 if request.image else None,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"run_id": run.id, "run": testlab.run_to_dict(run)}


@router.get("/api/testlab/runs")
async def list_test_runs():
    return {"runs": testlab.list_runs()}


@router.get("/api/testlab/runs/{run_id}")
async def get_test_run(run_id: str):
    return testlab.run_to_dict(_run(run_id))


@router.post("/api/testlab/runs/{run_id}/stop")
async def stop_test_run(run_id: str):
    _run(run_id)
    run = testlab.stop_run(run_id)
    return testlab.run_to_dict(run)
