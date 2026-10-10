"""Structural and DNS checks used in addition to a mandatory egress proxy."""

from __future__ import annotations

import asyncio
import ipaddress
import socket

from web_agent.core.errors import PolicyViolationError
from web_agent.core.schemas import SecurityProfile
from web_agent.security.origins import Origin, OriginError, is_private_or_local_host, parse_url_origin


class NetworkPolicyValidator:
    """Fail closed on URL, DNS, and proxy-boundary violations.

    DNS validation is defence in depth only.  The proxy is mandatory because it
    is the component that binds DNS validation to the actual outbound socket and
    can therefore defeat rebinding races.
    """

    def __init__(self, profile: SecurityProfile):
        self.profile = profile
        self.allowed_origins: set[Origin] = {
            parse_url_origin(origin) for origin in self.profile.allowed_origins
        }
        self.egress_proxy = (
            self.profile.egress_proxy.server if self.profile.egress_proxy else None
        )

    @property
    def proxy_options(self) -> dict[str, str] | None:
        return {"server": self.egress_proxy} if self.egress_proxy else None

    def is_url_allowed(self, target_url: str) -> bool:
        """Validate only the stable URL/origin portion of the policy."""
        try:
            target_origin = parse_url_origin(target_url)
        except OriginError:
            return False
        return (
            bool(self.allowed_origins)
            and target_origin in self.allowed_origins
            and not is_private_or_local_host(target_origin.host)
        )

    async def is_request_allowed(self, target_url: str) -> bool:
        if not self.is_url_allowed(target_url):
            return False
        try:
            origin = parse_url_origin(target_url)
            addresses = await asyncio.to_thread(
                socket.getaddrinfo,
                origin.host,
                origin.port,
                type=socket.SOCK_STREAM,
            )
        except (OSError, OriginError):
            return False

        if not addresses:
            return False
        for _family, _kind, _proto, _canonname, sockaddr in addresses:
            try:
                address = ipaddress.ip_address(sockaddr[0].split("%", 1)[0])
            except ValueError:
                return False
            if (
                not address.is_global
                or address.is_multicast
                or address.is_loopback
                or address.is_private
                or address.is_reserved
                or address.is_link_local
            ):
                return False
        return True

    async def require_request_allowed(self, target_url: str) -> None:
        if not self.egress_proxy:
            raise PolicyViolationError(
                "La navegación real requiere un proxy de salida impuesto por infraestructura."
            )
        if not await self.is_request_allowed(target_url):
            raise PolicyViolationError("Destino no autorizado o resolución DNS insegura.")
