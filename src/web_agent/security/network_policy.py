"""Política única de red para HTTP(S) y WebSocket (documento maestro §11.4).

- Bloqueo por defecto; solo orígenes permitidos y el origen exacto del escritorio.
- WebSockets autorizados únicamente hacia el escritorio (host, puerto y, si se
  configura, prefijo de ruta). No se abren WebSockets indiscriminadamente.
- Comprobación DNS como defensa en profundidad (no sustituye un proxy de
  salida de infraestructura, que sigue siendo recomendable en despliegue).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from urllib.parse import urlsplit

from web_agent.core.errors import PolicyViolationError
from web_agent.security.origins import (
    Origin,
    OriginError,
    classify_ip,
    ip_allowed_for_desktop,
    is_private_or_local_host,
    parse_url_origin,
)

_DNS_TTL_SECONDS = 30.0


class NetworkPolicyValidator:
    def __init__(self, profile):  # profile: SecurityProfile (import diferido para evitar ciclos)
        self.profile = profile
        self.allowed_origins: set[Origin] = {parse_url_origin(o) for o in profile.allowed_origins}
        self.desktop_origin: Origin | None = (
            parse_url_origin(profile.desktop_origin) if profile.desktop_origin else None
        )
        self.allow_private_desktop = bool(profile.allow_private_desktop)
        self.allow_loopback_desktop = bool(profile.allow_loopback_desktop)
        self.websocket_path_prefixes = tuple(profile.websocket_path_prefixes)
        self.egress_proxy = profile.egress_proxy.server if profile.egress_proxy else None
        self._dns_cache: dict[tuple[str, int], tuple[float, bool, bool]] = {}
        self.blocked_count = 0

    @property
    def proxy_options(self) -> dict[str, str] | None:
        return {"server": self.egress_proxy} if self.egress_proxy else None

    # ------------------------------------------------------------------ URL
    def _origin_allowed(self, origin: Origin) -> bool:
        if self.desktop_origin is not None and origin == self.desktop_origin:
            return True
        return origin in self.allowed_origins and not is_private_or_local_host(origin.host)

    def is_url_allowed(self, target_url: str) -> bool:
        """Comprobación estructural (sin DNS) de URL HTTP(S)."""
        try:
            origin = parse_url_origin(target_url)
        except OriginError:
            return False
        return self._origin_allowed(origin)

    def is_desktop_url(self, target_url: str) -> bool:
        try:
            return self.desktop_origin is not None and parse_url_origin(target_url) == self.desktop_origin
        except OriginError:
            return False

    def is_websocket_allowed(self, ws_url: str) -> bool:
        """Solo WS(S) hacia el escritorio autorizado (o un origen permitido explícito)."""
        try:
            parts = urlsplit(ws_url)
            if parts.scheme.lower() not in ("ws", "wss"):
                return False
            origin = parse_url_origin(ws_url, allow_websocket=True)
        except (OriginError, ValueError):
            return False
        if not self._origin_allowed(origin):
            return False
        if self.websocket_path_prefixes:
            path = parts.path or "/"
            if not any(path.startswith(p) for p in self.websocket_path_prefixes):
                return False
        return True

    def websocket_guard_config(self) -> dict:
        """Orígenes (scheme://host:port, ws->http) y prefijos de ruta permitidos para WebSocket.

        Lo consume el guardián en página del navegador: misma regla que ``is_websocket_allowed``.
        """
        origins = set()
        if self.desktop_origin is not None:
            origins.add(self.desktop_origin)
        origins |= {o for o in self.allowed_origins if not is_private_or_local_host(o.host)}
        return {
            "origins": sorted(f"{o.scheme}://{o.host}:{o.port}" for o in origins),
            "prefixes": list(self.websocket_path_prefixes),
        }

    def resolver_hosts(self) -> list[str]:
        """Nombres de host que el navegador puede resolver (vacío si hay IP literales o proxy)."""
        if self.egress_proxy:
            return []
        hosts = []
        origins = ([self.desktop_origin] if self.desktop_origin else []) + sorted(self.allowed_origins)
        for o in origins:
            if o.host.replace(".", "").isdigit() or ":" in o.host:
                return []  # IP literal: las reglas de resolución no aplican; se confía en las demás capas
            hosts.append(o.host)
        return hosts

    # ------------------------------------------------------------------ DNS
    async def _resolve_ok(self, origin: Origin) -> bool:
        key = (origin.host, origin.port)
        now = time.monotonic()
        cached = self._dns_cache.get(key)
        if cached and now - cached[0] < _DNS_TTL_SECONDS:
            return cached[1]
        is_desktop = self.desktop_origin is not None and origin == self.desktop_origin
        ok = await self._resolve_uncached(origin, is_desktop)
        self._dns_cache[key] = (now, ok, is_desktop)
        return ok

    async def _resolve_uncached(self, origin: Origin, is_desktop: bool) -> bool:
        try:
            literal = ipaddress.ip_address(origin.host)
        except ValueError:
            literal = None
        if literal is not None:
            addresses = [literal]
        else:
            try:
                infos = await asyncio.to_thread(
                    socket.getaddrinfo, origin.host, origin.port, type=socket.SOCK_STREAM
                )
            except OSError:
                return False
            addresses = []
            for _f, _k, _p, _c, sockaddr in infos:
                try:
                    addresses.append(ipaddress.ip_address(str(sockaddr[0]).split("%", 1)[0]))
                except ValueError:
                    return False
        if not addresses:
            return False
        for address in addresses:
            if is_desktop:
                if not ip_allowed_for_desktop(
                    address,
                    allow_private=self.allow_private_desktop,
                    allow_loopback=self.allow_loopback_desktop,
                ):
                    return False
            elif classify_ip(address) != "global":
                return False
        return True

    async def is_request_allowed(self, target_url: str) -> bool:
        if not self.is_url_allowed(target_url):
            return False
        try:
            origin = parse_url_origin(target_url)
        except OriginError:
            return False
        return await self._resolve_ok(origin)

    async def is_websocket_request_allowed(self, ws_url: str) -> bool:
        if not self.is_websocket_allowed(ws_url):
            return False
        try:
            origin = parse_url_origin(ws_url, allow_websocket=True)
        except OriginError:
            return False
        return await self._resolve_ok(origin)

    async def require_request_allowed(self, target_url: str) -> None:
        if not await self.is_request_allowed(target_url):
            raise PolicyViolationError("Destino no autorizado o resolución DNS insegura.")
