"""Normalización estricta de orígenes y detección de destinos privados (Amenaza 2).

Este módulo no depende de ningún otro módulo del proyecto para que pueda ser
utilizado tanto por los esquemas Pydantic como por el validador de red sin
crear dependencias circulares.

Reglas aplicadas:
- Solo se admiten los esquemas ``http`` y ``https``; ``file:``, ``data:``,
  ``javascript:``, ``chrome:``, etc. se rechazan siempre.
- El origen se compara como la tupla (esquema, host, puerto efectivo). Comparar
  solo el *hostname* permitía degradar ``https`` a ``http`` o saltar a otro puerto.
- Se rechazan credenciales embebidas (``https://user@host``), barras invertidas,
  espacios y caracteres de control (Chromium normaliza ``\\`` como ``/``).
- Se bloquean destinos privados, locales, de enlace local y formas IPv4 no
  canónicas (``2130706433``, ``0x7f.1``) que Chromium interpreta como IP.
"""

from __future__ import annotations

import ipaddress
import re
from typing import NamedTuple
from urllib.parse import urlsplit

ALLOWED_SCHEMES = frozenset({"https", "http"})
MAX_URL_LENGTH = 2048
_DEFAULT_PORTS = {"https": 443, "http": 80}

_BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "metadata",
        "metadata.google.internal",
        "instance-data",
    }
)
_BLOCKED_HOST_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".localdomain",
    ".home.arpa",
    ".lan",
    ".intranet",
    ".corp",
)
_HOST_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
# Formas IPv4 no canónicas que Chromium resuelve como direcciones IP.
_NUMERIC_HOST = re.compile(r"^(?:0x[0-9a-f]*|\d+)(?:\.(?:0x[0-9a-f]*|\d+)){0,3}\.?$")
_FORBIDDEN_CHARS = re.compile(r"[\s\\\x00-\x1f\x7f]")


class OriginError(ValueError):
    """La URL u origen no cumple la política de normalización."""


class Origin(NamedTuple):
    scheme: str
    host: str
    port: int

    def serialize(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        if _DEFAULT_PORTS.get(self.scheme) == self.port:
            return f"{self.scheme}://{host}"
        return f"{self.scheme}://{host}:{self.port}"


def _ip_or_none(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None


def is_private_or_local_host(host: str) -> bool:
    """Devuelve True si el host apunta (o puede apuntar) a la red local/privada."""
    h = host.lower().rstrip(".").strip("[]")
    if not h:
        return True
    if h in _BLOCKED_HOSTS or h.endswith(_BLOCKED_HOST_SUFFIXES):
        return True
    ip = _ip_or_none(h)
    if ip is None:
        # Un host "numérico" que no es una IP canónica se trata como intento de
        # evasión (p. ej. 2130706433 == 127.0.0.1 para Chromium).
        return bool(_NUMERIC_HOST.fullmatch(h))
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (not ip.is_global) or ip.is_multicast


def _normalize_host(raw_host: str) -> str:
    host = raw_host.rstrip(".")
    if not host:
        raise OriginError("host vacío")
    if "*" in host:
        raise OriginError("no se admiten comodines en el host")
    if _ip_or_none(host) is not None:
        return host
    try:
        ascii_host = host.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise OriginError("host IDN inválido") from exc
    labels = ascii_host.split(".")
    if not all(_HOST_LABEL.fullmatch(label) for label in labels):
        raise OriginError("host con etiquetas DNS inválidas")
    return ascii_host


def parse_url_origin(url: str) -> Origin:
    """Extrae el origen normalizado de una URL de destino o lanza OriginError."""
    if not isinstance(url, str) or not url:
        raise OriginError("URL vacía")
    if len(url) > MAX_URL_LENGTH:
        raise OriginError("URL demasiado larga")
    if _FORBIDDEN_CHARS.search(url):
        raise OriginError("URL con espacios, barras invertidas o caracteres de control")
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise OriginError(f"esquema no permitido: {scheme or '(ninguno)'}")
    if not parts.netloc:
        raise OriginError("URL sin autoridad (host)")
    if "@" in parts.netloc:
        raise OriginError("no se admiten credenciales embebidas en la URL")
    if parts.hostname is None:
        raise OriginError("URL sin host")
    host = _normalize_host(parts.hostname)
    try:
        port = parts.port
    except ValueError as exc:
        raise OriginError("puerto inválido") from exc
    return Origin(scheme, host, port if port is not None else _DEFAULT_PORTS[scheme])


def parse_allowed_origin(origin: str) -> Origin:
    """Valida una entrada de ``allowed_origins`` de un perfil."""
    parsed = parse_url_origin(origin)
    parts = urlsplit(origin)
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise OriginError("un origen permitido no puede incluir ruta, query ni fragmento")
    if is_private_or_local_host(parsed.host):
        raise OriginError("no se permiten orígenes privados, locales o de enlace local")
    return parsed
