"""Observador visual, modo principal (documento maestro §8.5, §9; ADR-001).

Captura el viewport visible (nunca página completa) en escala CSS, de modo que
las dimensiones de la imagen coinciden con el viewport y la transformación es
la identidad. No inspecciona el DOM de la aplicación: la URL y el tamaño del
viewport son metadatos operativos, no inspección semántica.
"""

from __future__ import annotations

import hashlib
import time

from web_agent.core.errors import ObservationError
from web_agent.core.geometry import GeometryError, image_dimensions
from web_agent.core.observation import ImageTransform, VisualObservation, new_observation_id


class VisualObserver:
    @staticmethod
    async def capture(
        page,
        *,
        task_id: str | None,
        run_id: str,
        session_id: str,
        page_id: str,
        navigation_epoch: int,
        geometry_revision: int,
        max_image_bytes: int,
        timeout_ms: float = 10_000,
        sensitive: bool = False,
    ) -> tuple[VisualObservation, bytes]:
        viewport = page.viewport_size
        if not viewport or viewport.get("width", 0) < 1 or viewport.get("height", 0) < 1:
            raise ObservationError("Viewport desconocido; no se puede establecer la geometría.")
        url = page.url
        warnings: list[str] = []

        data = await page.screenshot(type="png", full_page=False, scale="css", timeout=timeout_ms)
        if len(data) > max_image_bytes:
            # Misma geometría, menor tamaño: JPEG conserva legibilidad razonable.
            data = await page.screenshot(type="jpeg", quality=80, full_page=False, scale="css", timeout=timeout_ms)
            warnings.append("captura_comprimida_jpeg")
        if len(data) > max_image_bytes:
            raise ObservationError("La captura supera el tamaño máximo permitido.")

        try:
            width, height, fmt = image_dimensions(data)
        except GeometryError as exc:
            raise ObservationError("Captura con formato no reconocido.") from exc

        obs_id = new_observation_id()
        try:
            observation = VisualObservation(
                observation_id=obs_id,
                task_id=task_id,
                run_id=run_id,
                session_id=session_id,
                page_id=page_id,
                navigation_epoch=navigation_epoch,
                geometry_revision=geometry_revision,
                captured_at_monotonic=time.monotonic(),
                page_url=url,
                viewport_width_css=int(viewport["width"]),
                viewport_height_css=int(viewport["height"]),
                image_width_px=width,
                image_height_px=height,
                image_format=fmt,  # type: ignore[arg-type]
                image_sha256=hashlib.sha256(data).hexdigest(),
                image_bytes_len=len(data),
                image_reference=f"mem:{obs_id}",
                transform=ImageTransform(),
                sensitive=sensitive,
                warnings=tuple(warnings),
            )
        except ValueError as exc:
            raise ObservationError("La captura no es coherente con la geometría del viewport.") from exc
        return observation, data
