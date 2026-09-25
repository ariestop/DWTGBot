"""
Public temp-link endpoint: ``GET /d/{token}``.

Flow:
  1. Look up the token in DB.
  2. Validate active / not expired / not exhausted.
  3. If the request starts a new download, atomically increment
     downloads_count and deactivate when the limit is reached.
     Continuations (``Range`` not starting at byte 0, ``HEAD``) of an
     already started download are free — see ``starts_new_download``.
  4. Stream the file back. If running behind nginx with the
     ``X-Accel-Redirect`` location ``/_protected/``, we delegate the actual
     bytes to nginx for efficient sendfile().

Security:
  - Tokens are 32+ bytes URL-safe (configurable).
  - File path is validated against STORAGE_PATH every time (defense in depth).
  - Endpoint never lists files; it only resolves tokens to a single path.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import FileResponse

from app.composition import ApiComposition
from app.config import Settings
from app.domain.observability import TempLinkServeResult
from app.exceptions import StorageError
from app.logging_config import get_logger
from app.utils.filenames import ensure_within

router = APIRouter()
_logger = get_logger(__name__)

XACCEL_HEADER = "X-Accel-Redirect"
XACCEL_LOCATION = "/_protected/"


def _composition(request: Request) -> ApiComposition:
    return request.app.state.composition


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def starts_new_download(method: str, range_header: str | None) -> bool:
    """Whether a request should spend one ``downloads_count`` slot.

    Video players and download managers fetch one file with many Range
    requests (seek, resume, moov-at-end probing). Charging each of them
    exhausted a 5-use link during the first playback, cutting the file
    mid-stream. Only a request that reads from byte 0 counts; an
    unparseable Range is treated as a full read, so it counts too.
    """
    if method != "GET":
        return False
    if not range_header:
        return True
    unit, _, spec = range_header.partition("=")
    if unit.strip().lower() != "bytes":
        return True
    first = spec.split(",", 1)[0].strip()
    start, sep, _ = first.partition("-")
    if not sep:
        return True
    start = start.strip()
    if not start:
        return False  # suffix range ``bytes=-N`` — the tail of the file
    return not start.isdigit() or int(start) == 0


@router.api_route("/d/{token}", methods=["GET", "HEAD"])
async def download(token: str, request: Request) -> Response:
    composition: ApiComposition = _composition(request)
    settings: Settings = _settings(request)
    metrics = composition.job_metrics

    # Truncated token used in every log/metric path — full value would
    # leak into operator logs and defeat the point of single-use links.
    token_prefix = token[:8] + "..."

    # Audit fix #5: do the cheap, read-only checks first — token
    # existence, file path safety, file presence — *before* the atomic
    # increment. That way a path-traversal probe / cleanup-race never
    # consumes a download slot.
    pre_check = await composition.temp_links_repo.get_by_token(token)
    if pre_check is None:
        # Possible enumeration attempt — nginx limit_req already
        # absorbs the bulk of it (docs/17- §2 row "Token guessing"),
        # this counter exposes whether anything is leaking through.
        metrics.inc_temp_link_serve(result=TempLinkServeResult.NOT_FOUND)
        _logger.info("temp_link_served", token=token_prefix, result="not_found")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link not found")

    try:
        file_path = ensure_within(settings.STORAGE_PATH, Path(pre_check.file_path))
    except StorageError as exc:
        # Security-grade — alert on this counter at any non-zero rate.
        metrics.inc_temp_link_serve(result=TempLinkServeResult.FORBIDDEN)
        _logger.error("temp_link_path_invalid", token=token_prefix, path=pre_check.file_path)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid path") from exc

    if not file_path.exists() or not file_path.is_file():
        # Cleanup race — the worker said the file existed at job-done
        # time, but it has since been removed. Distinct from EXPIRED
        # because this *should* count against A6.
        metrics.inc_temp_link_serve(result=TempLinkServeResult.GONE)
        _logger.warning("temp_link_served", token=token_prefix, result="gone")
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="File no longer available")

    counted = starts_new_download(request.method, request.headers.get("range"))
    if counted:
        # Single atomic UPDATE ... RETURNING — closes the race that the
        # previous load/mutate/save pair left open: two parallel requests
        # on a max=1 link could both see ``downloads_count == 0`` and both
        # be served. Now Postgres serialises the row update for us.
        link = await composition.temp_links_repo.try_register_use(token)
    elif pre_check.is_usable() or pre_check.can_resume():
        link = pre_check
    else:
        link = None
    if link is None:
        # By-design expiry / exhaustion (or a race we just lost).
        # Excluded from the A6 denominator at query time
        # (ADR-0007 §2.8) — would otherwise tank a legitimate
        # "user clicked twice" SLO.
        metrics.inc_temp_link_serve(result=TempLinkServeResult.EXPIRED)
        _logger.info("temp_link_served", token=token_prefix, result="expired", counted=counted)
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Link expired or exhausted")

    metrics.inc_temp_link_serve(result=TempLinkServeResult.OK)
    _logger.info(
        "temp_link_served",
        token=token_prefix,
        result="ok",
        counted=counted,
        downloads=link.downloads_count,
        max=link.max_downloads,
        active=link.is_active,
    )

    # Prefer X-Accel-Redirect when behind nginx — nginx serves the file
    # via sendfile() while keeping our auth in front. The actual filesystem
    # path under nginx is the same STORAGE_PATH; we expose only its relative tail.
    #
    # S6 (audit fix): ``XACCEL_ENABLED`` is the trust gate. If it is
    # ``False`` we *must not* honour the header — otherwise any client
    # can set ``X-Internal-XAccel: 1`` against a directly-reachable api
    # and pivot through ``/_protected/`` (when nginx is wired but the
    # api is also exposed). The bundled nginx config sets this header
    # itself and overwrites whatever the client sent (see media.conf
    # template), so the runtime semantics are unchanged.
    use_xaccel = settings.XACCEL_ENABLED and request.headers.get("x-internal-xaccel", "0") == "1"
    if use_xaccel:
        rel = file_path.relative_to(settings.STORAGE_PATH)
        response = Response(status_code=200)
        response.headers[XACCEL_HEADER] = f"{XACCEL_LOCATION}{rel.as_posix()}"
        response.headers["Content-Disposition"] = f'attachment; filename="{file_path.name}"'
        return response

    return FileResponse(
        path=str(file_path),
        filename=file_path.name,
        media_type="application/octet-stream",
    )
