"""Autenticación Bearer (documento maestro §8.1).

Comparación en tiempo constante. Si el servicio no tiene token configurado,
falla cerrado (503) en lugar de aceptar peticiones sin autenticar.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)


def require_bearer(request: Request) -> None:
    settings = request.app.state.settings
    expected = settings.bearer_token
    if not expected:
        logger.warning(
            "require_bearer: 503 Service Unavailable porque no hay token configurado en el servidor "
            "(defina EVALUATION_BEARER_TOKEN o TEAM_TOKEN en las variables de entorno)"
        )
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
