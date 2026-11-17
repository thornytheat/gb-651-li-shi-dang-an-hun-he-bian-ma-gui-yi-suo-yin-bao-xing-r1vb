from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from . import service

router = APIRouter(tags=["M7-安全回收"])


@router.post("/api/reclaim")
async def reclaim(request: Request):
    """回收同一份证据表：核验配套、逐字还原、整批拒收或幂等入库。"""
    raw = await request.body()
    result = service.submit(request.app.state.store, raw)
    return JSONResponse(content=result)


@router.get("/api/reclaim/jobs")
def reclaim_jobs(request: Request):
    return {"jobs": service.jobs(request.app.state.store)}


@router.get("/api/reclaim/jobs/{job_id}")
def reclaim_job(request: Request, job_id: str):
    return service.job_detail(request.app.state.store, job_id)


@router.post("/api/reclaim/jobs/{job_id}/resume")
def reclaim_resume(request: Request, job_id: str):
    """中断重启后续跑，已完成的作业直接回显，不重复入库。"""
    return service.resume(request.app.state.store, job_id)
