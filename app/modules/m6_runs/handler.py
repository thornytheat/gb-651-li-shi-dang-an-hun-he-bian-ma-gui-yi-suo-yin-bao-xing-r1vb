from urllib.parse import quote
from fastapi import APIRouter, Request
from fastapi.responses import Response
from .service import execute
from . import core

router = APIRouter(tags=["M6-批次导出"])


@router.get("/runs")
def page(request: Request):
    data = execute({"store": request.app.state.store})
    evidence = request.app.state.store.latest_evidence()
    data["evidence_run_id"] = evidence["run_id"] if evidence else data["run_id"]
    return request.app.state.templates.TemplateResponse(
        request, "m6_runs.html", {"title": "批次导出", "data": data})


@router.get("/api/runs")
def runs(request: Request):
    execute({"store": request.app.state.store})
    return {"runs": request.app.state.store.all()}


@router.get("/api/export/evidence.csv")
def export(request: Request, run_id: str | None = None):
    rid, text = core.safe_export(request.app.state.store, run_id)
    filename = f"evidence-{rid}.csv"
    return Response(
        text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition":
                f"attachment; filename={filename}; filename*=UTF-8''{quote(filename)}",
            "X-Evidence-Run-Id": rid,
        },
    )
