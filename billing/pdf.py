from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from django.http import HttpResponse
from django.template.loader import render_to_string


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

    try:
        import weasyprint  # type: ignore
    except ModuleNotFoundError:
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
