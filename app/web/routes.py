"""
Interfaccia web interna (2026-10-14), servita dallo stesso FastAPI sotto
/ui. Pagine HTML renderizzate sul server (Jinja2): niente applicazione
frontend separata da costruire, versionare e ospitare.

Accesso: se DASHBOARD_API_KEY è impostata, login con la chiave condivisa
più un nome (finisce in ProspectStatus.changed_by, così si sa chi ha
approvato cosa). Non è un sistema di utenti con permessi: per un piccolo
team interno basta; se servono permessi diversi per persona, va
sostituito.
"""
from __future__ import annotations

import hashlib
import hmac
import uuid
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.database.session import get_session
from app.models import AgentRun, EvidenceFile, ResearchJob
from app.services.audit_builder import build_audit_package, describe_issue, latest_audit_package
from app.services.prospect_service import (
    HUMAN_STATUSES,
    STATUS_LABELS,
    ProspectNotFoundError,
    confirm_website,
    dashboard_counts,
    generate_ai_for_brand,
    get_prospect,
    list_prospects,
    save_draft_edit,
    set_status,
)

_HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(_HERE / "templates"))
templates.env.globals["STATUS_LABELS"] = STATUS_LABELS
templates.env.filters["issue_text"] = lambda raw: describe_issue(raw)["problem"]
templates.env.filters["urlquote"] = lambda value: quote(str(value or ""), safe="")

router = APIRouter(prefix="/ui", include_in_schema=False)

_KEY_COOKIE = "dinamik_key"
_USER_COOKIE = "dinamik_user"
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class _LoginRequired(Exception):
    pass


def _key_digest(key: str) -> str:
    return hashlib.sha256(f"dinamik-ui:{key}".encode()).hexdigest()


def current_user(request: Request) -> str:
    key = get_settings().dashboard_api_key
    if key:
        cookie = request.cookies.get(_KEY_COOKIE, "")
        if not hmac.compare_digest(cookie, _key_digest(key)):
            raise _LoginRequired()
    return request.cookies.get(_USER_COOKIE) or "team"


UserDep = Annotated[str, Depends(current_user)]


def _render(request: Request, name: str, context: dict[str, Any], status_code: int = 200) -> HTMLResponse:
    context.setdefault("user", request.cookies.get(_USER_COOKIE) or "team")
    context.setdefault("flash", request.query_params.get("msg"))
    context.setdefault("flash_error", request.query_params.get("err"))
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def _back(path: str, msg: str | None = None, err: str | None = None) -> RedirectResponse:
    query = ""
    if msg:
        query = f"?msg={quote(msg)}"
    elif err:
        query = f"?err={quote(err)}"
    return RedirectResponse(f"{path}{query}", status_code=303)


# --- login -------------------------------------------------------------------


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    return _render(request, "login.html", {"needs_key": bool(get_settings().dashboard_api_key)})


@router.post("/login")
async def login(
    request: Request, name: Annotated[str, Form()] = "", key: Annotated[str, Form()] = ""
) -> Response:
    configured = get_settings().dashboard_api_key
    if configured and not hmac.compare_digest(key, configured):
        return _render(request, "login.html", {"needs_key": True, "flash_error": "Chiave non valida"}, 401)
    response = RedirectResponse("/ui", status_code=303)
    if configured:
        response.set_cookie(_KEY_COOKIE, _key_digest(configured), httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    response.set_cookie(_USER_COOKIE, (name.strip() or "team")[:60], samesite="lax", max_age=60 * 60 * 24 * 365)
    return response


@router.get("/logout")
async def logout() -> Response:
    response = RedirectResponse("/ui/login", status_code=303)
    response.delete_cookie(_KEY_COOKIE)
    return response


# --- pagine ------------------------------------------------------------------


async def _scout_runs(session: AsyncSession, limit: int) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(ResearchJob, AgentRun)
            .join(AgentRun, AgentRun.job_id == ResearchJob.id)
            .where(AgentRun.agent_name == "amazon_scout_live")
            .order_by(ResearchJob.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [{"job": job, "output": run.output or {}} for job, run in rows]


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, session: SessionDep, user: UserDep) -> HTMLResponse:
    counts = await dashboard_counts(session)
    top = await list_prospects(session, limit=10)
    runs = await _scout_runs(session, 5)
    return _render(request, "dashboard.html", {"counts": counts, "top": top, "runs": runs, "active": "dashboard"})


@router.get("/prospects", response_class=HTMLResponse)
async def prospects(
    request: Request,
    session: SessionDep,
    user: UserDep,
    status: str | None = None,
    q: str | None = None,
    all: int = 0,
) -> HTMLResponse:
    rows = await list_prospects(session, status=status or None, search=q or None, target_only=not all, limit=300)
    return _render(
        request,
        "prospects.html",
        {"rows": rows, "status": status or "", "q": q or "", "all": all, "statuses": ["NEW", *HUMAN_STATUSES],
         "active": "prospects"},
    )


@router.get("/prospects/{brand_id}", response_class=HTMLResponse)
async def prospect_detail(request: Request, brand_id: uuid.UUID, session: SessionDep, user: UserDep) -> HTMLResponse:
    try:
        detail = await get_prospect(session, brand_id)
    except ProspectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="prospect non trovato") from exc
    audit = await latest_audit_package(session, brand_id)
    return _render(
        request,
        "prospect.html",
        {"d": detail, "audit": audit, "human_statuses": HUMAN_STATUSES, "active": "prospects"},
    )


@router.post("/prospects/{brand_id}/status")
async def prospect_status(
    brand_id: uuid.UUID,
    session: SessionDep,
    user: UserDep,
    status: Annotated[str, Form()],
    reason: Annotated[str, Form()] = "",
) -> Response:
    try:
        await set_status(session, brand_id, status, reason=reason, changed_by=user)
    except ValueError as exc:
        return _back(f"/ui/prospects/{brand_id}", err=str(exc))
    await session.commit()
    return _back(f"/ui/prospects/{brand_id}", msg=f"Stato aggiornato: {STATUS_LABELS.get(status, status)}")


@router.post("/prospects/{brand_id}/draft")
async def prospect_draft(
    brand_id: uuid.UUID,
    session: SessionDep,
    user: UserDep,
    subject: Annotated[str, Form()],
    body: Annotated[str, Form()],
) -> Response:
    await save_draft_edit(session, brand_id, subject=subject, body=body, changed_by=user)
    await session.commit()
    return _back(f"/ui/prospects/{brand_id}", msg="Bozza salvata")


@router.post("/prospects/{brand_id}/contacts")
async def prospect_contacts(brand_id: uuid.UUID, session: SessionDep, user: UserDep) -> Response:
    from app.models import Brand, Company
    from app.services.contact_discovery import discover_contacts, save_contacts

    brand = await session.get(Brand, brand_id)
    if brand is None or not brand.website:
        return _back(f"/ui/prospects/{brand_id}", err="Serve prima un sito confermato")
    company = await session.get(Company, brand.company_id) if brand.company_id else None
    exclude = {company.vat_number} if company and company.vat_number else set()
    result = await discover_contacts(brand.website, exclude_digits=exclude)
    created = await save_contacts(session, brand, result.contacts)
    await session.commit()
    if not result.pages_read:
        return _back(f"/ui/prospects/{brand_id}", err="Sito non raggiungibile")
    return _back(f"/ui/prospects/{brand_id}", msg=f"{created} contatti nuovi da {len(result.pages_read)} pagine")


@router.post("/prospects/{brand_id}/website")
async def prospect_website(
    brand_id: uuid.UUID, session: SessionDep, user: UserDep, url: Annotated[str, Form()]
) -> Response:
    from app.tools.website.provider import WebsiteIntelligenceProvider

    if not url.strip():
        return _back(f"/ui/prospects/{brand_id}", err="Indirizzo del sito vuoto")
    await confirm_website(session, brand_id, url, provider=WebsiteIntelligenceProvider())
    await session.commit()
    return _back(f"/ui/prospects/{brand_id}", msg="Sito confermato e analizzato")


@router.post("/prospects/{brand_id}/ai")
async def prospect_ai(brand_id: uuid.UUID, session: SessionDep, user: UserDep) -> Response:
    outcome = await generate_ai_for_brand(session, brand_id)
    await session.commit()
    if outcome.ai_disabled:
        return _back(f"/ui/prospects/{brand_id}", err="AI non disponibile: Ollama non configurato o spento")
    if outcome.draft_id is None and outcome.recommendations_created == 0:
        return _back(f"/ui/prospects/{brand_id}", err="Nessuna raccomandazione generata (vedi log errori)")
    return _back(f"/ui/prospects/{brand_id}", msg="Raccomandazioni e bozza generate")


@router.post("/prospects/{brand_id}/audit")
async def prospect_build_audit(brand_id: uuid.UUID, session: SessionDep, user: UserDep) -> Response:
    try:
        await build_audit_package(session, brand_id)
    except ProspectNotFoundError as exc:
        return _back(f"/ui/prospects/{brand_id}", err=str(exc))
    await session.commit()
    return RedirectResponse(f"/ui/prospects/{brand_id}/audit", status_code=303)


@router.get("/prospects/{brand_id}/audit", response_class=HTMLResponse)
async def prospect_audit(request: Request, brand_id: uuid.UUID, session: SessionDep, user: UserDep) -> Response:
    package = await latest_audit_package(session, brand_id)
    if package is None:
        return _back(f"/ui/prospects/{brand_id}", err="Nessun audit ancora generato")
    return _render(request, "audit.html", {"p": package.payload, "package": package, "active": "prospects"})


@router.get("/runs", response_class=HTMLResponse)
async def runs(request: Request, session: SessionDep, user: UserDep) -> HTMLResponse:
    return _render(request, "runs.html", {"runs": await _scout_runs(session, 100), "active": "runs"})


@router.get("/files/{file_id}")
async def evidence_file(file_id: uuid.UUID, session: SessionDep, user: UserDep) -> Response:
    row = await session.get(EvidenceFile, file_id)
    if row is None:
        raise HTTPException(status_code=404)
    return Response(content=row.data, media_type=row.content_type, headers={"Cache-Control": "private, max-age=86400"})
