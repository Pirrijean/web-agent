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
from urllib.parse import urlencode, urlsplit, urlunsplit

ALLOWED_SCHEMES = frozenset({"https", "http"})
MAX_URL_LENGTH = 2048
_DEFAULT_PORTS = {"https": 443, "http": 80}
# Los WebSockets se comparan con el origen HTTP equivalente.
WS_SCHEME_MAP = {"ws": "http", "wss": "https"}
# Nombres que nunca se autorizan, ni siquiera como escritorio explícito.
_NEVER_ALLOWED_HOSTS = frozenset({"metadata", "metadata.google.internal", "instance-data"})

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
    ip = _ip_or_none(host)
    if ip is not None:
        return str(ip)
    try:
        ascii_host = host.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise OriginError("host IDN inválido") from exc
    labels = ascii_host.split(".")
    if not all(_HOST_LABEL.fullmatch(label) for label in labels):
        raise OriginError("host con etiquetas DNS inválidas")
    return ascii_host


def parse_url_origin(url: str, *, allow_websocket: bool = False) -> Origin:
    """Extrae el origen normalizado de una URL de destino o lanza OriginError.

    Con ``allow_websocket`` los esquemas ``ws``/``wss`` se normalizan a
    ``http``/``https`` para compararlos con los orígenes autorizados.
    """
    if not isinstance(url, str) or not url:
        raise OriginError("URL vacía")
    if len(url) > MAX_URL_LENGTH:
        raise OriginError("URL demasiado larga")
    if _FORBIDDEN_CHARS.search(url):
        raise OriginError("URL con espacios, barras invertidas o caracteres de control")
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise OriginError("URL malformada") from exc
    scheme = parts.scheme.lower()
    if allow_websocket and scheme in WS_SCHEME_MAP:
        scheme = WS_SCHEME_MAP[scheme]
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


def classify_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    """Clasifica una IP: global, private, loopback o forbidden (link-local, multicast...)."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        return "forbidden"
    if ip.is_global:
        return "global"
    if ip.is_private:
        return "private"
    return "forbidden"


def ip_allowed_for_desktop(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address, *, allow_private: bool, allow_loopback: bool
) -> bool:
    kind = classify_ip(ip)
    if kind == "global":
        return True
    if kind == "private":
        return allow_private
    if kind == "loopback":
        return allow_loopback
    return False


def parse_desktop_origin(url: str, *, allow_private: bool, allow_loopback: bool) -> Origin:
    """Origen exacto del escritorio suministrado por el evaluador autenticado (§11.4).

    Es una excepción estrecha: solo ese esquema/host/puerto. Puede ser privado
    si el operador lo habilita, loopback solo con habilitación explícita, y
    nunca enlace local, metadatos de nube, multicast o no especificado.
    """
    parsed = parse_url_origin(url)
    host = parsed.host
    if host in _NEVER_ALLOWED_HOSTS:
        raise OriginError("destino de metadatos no permitido")
    ip = _ip_or_none(host)
    if ip is not None:
        if not ip_allowed_for_desktop(ip, allow_private=allow_private, allow_loopback=allow_loopback):
            raise OriginError("dirección del escritorio no permitida por la política")
        return parsed
    if _NUMERIC_HOST.fullmatch(host):
        raise OriginError("host numérico no canónico")
    if host in _BLOCKED_HOSTS or host.endswith(".localhost"):
        if not allow_loopback:
            raise OriginError("host local no permitido")
        return parsed
    if is_private_or_local_host(host) and not allow_private:
        raise OriginError("host privado no permitido")
    return parsed


def viewer_url_for_desktop(url: str) -> str:
    """URL que abre el navegador para un escritorio dado.

    El evaluador entrega el WebSocket VNC (``wss://host/websockify``). El agente opera
    a través del visor noVNC servido en la raíz del MISMO origen, indicándole la ruta
    del WebSocket. La contraseña nunca viaja en la URL: se introduce en el visor.
    Las URL http(s) se devuelven sin cambios.
    """
    url = url.strip()
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme in ("http", "https") and not parts.query and parts.path in ("", "/", "/vnc.html", "/websockify"):
        # Raíz del escritorio (noVNC sin parámetros se queda esperando): se abre el visor completo.
        if not parts.hostname or parts.username or parts.password:
            raise OriginError("URL de escritorio inválida")
        query = urlencode({"autoconnect": "1", "resize": "scale", "reconnect": "1", "path": "websockify"})
        return urlunsplit((scheme, parts.netloc, "/", query, ""))
    if scheme not in WS_SCHEME_MAP:
        return url
    if not parts.hostname:
        raise OriginError("WebSocket de escritorio sin host")
    if parts.username or parts.password:
        raise OriginError("credenciales embebidas en la URL")
    ws_path = parts.path.lstrip("/") or "websockify"
    if parts.query:
        ws_path = f"{ws_path}?{parts.query}"
    query = urlencode({"autoconnect": "1", "resize": "scale", "reconnect": "1", "path": ws_path})
    return urlunsplit((WS_SCHEME_MAP[scheme], parts.netloc, "/", query, ""))


def parse_allowed_origin(origin: str) -> Origin:
    """Valida una entrada de ``allowed_origins`` de un perfil."""
    parsed = parse_url_origin(origin)
    parts = urlsplit(origin)
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise OriginError("un origen permitido no puede incluir ruta, query ni fragmento")
    if is_private_or_local_host(parsed.host):
        raise OriginError("no se permiten orígenes privados, locales o de enlace local")
    return parsed
