from __future__ import annotations

import base64
import contextlib
import importlib
import io
import logging
import mimetypes
from functools import lru_cache
from pathlib import Path

from django.http import HttpResponse
from django.template.loader import render_to_string


logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _load_weasyprint():
    """Load the optional PDF backend once without repeating its import banner."""
    diagnostics = io.StringIO()
    try:
        # WeasyPrint prints a long native-library help banner before raising when
        # Pango/Harfbuzz cannot be loaded. Capture it and emit one actionable log.
        with contextlib.redirect_stderr(diagnostics):
            return importlib.import_module("weasyprint")
    except (ModuleNotFoundError, ImportError, OSError) as exc:
        logger.warning(
            "WeasyPrint is unavailable; serving printable HTML instead of PDF. "
            "Install its native libraries or use the Docker runtime. Cause: %s",
            exc,
        )
        return None


def build_image_data_uri(path: str | Path) -> str | None:
    p = Path(path)
    if not p.exists() or not p.is_file():
        return None
    mime, _ = mimetypes.guess_type(str(p))
    if not mime:
        mime = "application/octet-stream"
    data = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def render_pdf_or_html(
    *,
    request=None,
    template_name: str,
    context: dict,
    filename: str,
    as_attachment: bool = True,
) -> HttpResponse:
    html = render_to_string(template_name, context, request=request)
    base_url = request.build_absolute_uri("/") if request is not None else None

    weasyprint = _load_weasyprint()
    if weasyprint is None:
        response = HttpResponse(html, content_type="text/html; charset=utf-8")
        response["X-PDF-Fallback"] = "html"
        response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response["Pragma"] = "no-cache"
        response["Expires"] = "0"
        return response

    response = HttpResponse(content_type="application/pdf")
    disposition = "attachment" if as_attachment else "inline"
    response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    weasyprint.HTML(string=html, base_url=base_url, media_type="print").write_pdf(response)
    return response
