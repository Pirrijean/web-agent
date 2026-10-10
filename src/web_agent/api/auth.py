"""Autenticación Bearer (documento maestro §8.1).

Comparación en tiempo constante. Si el servicio no tiene token configurado,
falla cerrado (503) en lugar de aceptar peticiones sin autenticar.
"""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, status


def require_bearer(request: Request) -> None:
    settings = request.app.state.settings
    expected = settings.bearer_token
    if not expected:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Servicio sin token configurado.")
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token or not hmac.compare_digest(
        token.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="No autorizado.",
            headers={"WWW-Authenticate": "Bearer"},
        )
