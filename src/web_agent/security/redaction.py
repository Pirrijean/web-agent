"""Redacción de información sensible antes de registrar, auditar o enviar al modelo (Fase 5).

La versión anterior solo cubría correos y ``password=valor``; dejaba pasar tokens
Bearer, JWT, claves de API, cookies, parámetros ``?token=`` en URLs, tarjetas y
cualquier secreto real usado durante la ejecución. Esta versión añade:

- Patrones para credenciales comunes (Bearer/JWT/claves de proveedores/PEM).
- Pares clave-valor sensibles en texto libre, JSON, cabeceras y query strings.
- Redacción literal de secretos registrados en tiempo de ejecución
  (``register_secret``), de modo que un secreto concreto nunca aparece aunque
  no siga ningún patrón conocido.
- Redacción recursiva de estructuras (``redact_obj``).
"""

from __future__ import annotations

import re
import threading
from typing import Any

_SENSITIVE_KEY_WORDS = (
    r"password|passwd|pwd|pass|contraseña|contrasena|clave|secret|client[_-]?secret|"
    r"token|access[_-]?token|refresh[_-]?token|id[_-]?token|api[_-]?key|apikey|x-api-key|"
    r"auth|authorization|session(?:[_-]?id)?|sessid|cookie|set-cookie|private[_-]?key|"
    r"otp|pin|cvv|cvc|signature|sig"
)
SENSITIVE_KEY = re.compile(rf"(?i)^(?:{_SENSITIVE_KEY_WORDS})$")


class DataRedactor:
    """Oculta información sensible antes de guardarla en logs o auditoría (Fase 5)."""

    REDACTED = "[REDACTED]"
    MIN_LITERAL_SECRET_LEN = 4

    _PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
        (
            re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
            "[REDACTED_PRIVATE_KEY]",
        ),
        (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{6,}"), r"\1 [REDACTED]"),
        (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"), "[REDACTED_JWT]"),
        (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}"), "[REDACTED_KEY]"),
        (re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}"), "[REDACTED_KEY]"),
        (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_KEY]"),
        (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "[REDACTED_KEY]"),
        (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "[REDACTED_KEY]"),
        (
            re.compile(
                rf"(?i)([\"']?\b(?:{_SENSITIVE_KEY_WORDS})\b[\"']?\s*[:=]\s*)"
                r"(?:\"[^\"]*\"|'[^']*'|[^\s,;&}\]]+)"
            ),
            r"\1[REDACTED]",
        ),
        (re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"), "[REDACTED_EMAIL]"),
        (re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,3})?\b"), "[REDACTED_IBAN]"),
        (re.compile(r"(?<![\w.])(?:\d[ -]?){12,18}\d(?![\w.])"), "[REDACTED_NUMBER]"),
    )

    _literal_secrets: set[str] = set()
    _lock = threading.Lock()

    @classmethod
    def register_secret(cls, value: str) -> None:
        """Registra un secreto concreto para que se redacte literalmente en todo texto."""
        if isinstance(value, str) and len(value) >= cls.MIN_LITERAL_SECRET_LEN:
            with cls._lock:
                cls._literal_secrets.add(value)

    @classmethod
    def clear_registered_secrets(cls) -> None:
        with cls._lock:
            cls._literal_secrets.clear()

    @classmethod
    def unregister_secret(cls, value: str) -> None:
        with cls._lock:
            cls._literal_secrets.discard(value)

    @classmethod
    def redact_secrets_only(cls, text: str) -> str:
        """Redacta solo secretos literales registrados.

        Se usa para datos operativos enviados al modelo (p. ej. el objetivo):
        enmascarar un correo o un número alteraría el valor que la tarea debe
        introducir (§14). Los secretos reales nunca salen.
        """
        if not text:
            return text
        if not isinstance(text, str):
            text = str(text)
        with cls._lock:
            literals = sorted(cls._literal_secrets, key=len, reverse=True)
        for secret in literals:
            text = text.replace(secret, "[REDACTED_SECRET]")
        return text

    @classmethod
    def redact(cls, text: str) -> str:
        if not text:
            return text
        if not isinstance(text, str):
            text = str(text)
        with cls._lock:
            literals = sorted(cls._literal_secrets, key=len, reverse=True)
        for secret in literals:
            text = text.replace(secret, "[REDACTED_SECRET]")
        for pattern, replacement in cls._PATTERNS:
            text = pattern.sub(replacement, text)
        return text

    @classmethod
    def redact_obj(cls, obj: Any) -> Any:
        """Redacta recursivamente diccionarios, listas y cadenas."""
        if isinstance(obj, str):
            return cls.redact(obj)
        if isinstance(obj, dict):
            out: dict[Any, Any] = {}
            for key, value in obj.items():
                if isinstance(key, str) and SENSITIVE_KEY.match(key):
                    out[key] = cls.REDACTED
                else:
                    out[key] = cls.redact_obj(value)
            return out
        if isinstance(obj, (list, tuple)):
            return type(obj)(cls.redact_obj(v) for v in obj)
        return obj
