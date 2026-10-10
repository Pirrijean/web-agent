"""Reglas de evaluación del reto (frontier-reto-1/README.md, «Cómo se evalúa»).

Funciones puras para puntuar el resultado de una prueba a partir de:
- la respuesta de ``POST /__check`` del escritorio (estado de la app);
- el ``estado`` y ``resumen`` que reporta el agente;
- la definición de la prueba (``estado_esperado``, ``respuesta_esperada``, ``claves_razon``).

Las usa ``scripts/run_pruebas.py``; no forman parte del camino de ejecución del agente.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

_ANSWER_LINE = re.compile(r"^\s*respuesta\s*:\s*(.*)$", re.IGNORECASE | re.MULTILINE)
_NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class PruebaScore:
    prueba_id: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.ok for c in self.checks)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append(CheckResult(name, bool(ok), detail))


def answer_line(resumen: str) -> Optional[str]:
    m = _ANSWER_LINE.search(resumen or "")
    return m.group(1).strip() if m else None


def _norm(text: str) -> str:
    return " ".join(str(text).casefold().split())


def answer_matches(line: str, expected: str) -> bool:
    """Si lo esperado es numérico se compara el primer número de la línea; si no, el texto."""
    exp_num = _NUMBER.fullmatch(str(expected).strip())
    if exp_num:
        got = _NUMBER.search(line)
        if not got:
            return False
        return float(got.group().replace(",", ".")) == float(exp_num.group().replace(",", "."))
    parts_exp = [_norm(p) for p in str(expected).split(";")]
    parts_got = [_norm(p) for p in line.split(";")]
    return parts_got[: len(parts_exp)] == parts_exp


def score(prueba: dict, *, estado: Optional[str], resumen: Optional[str], app_check: Optional[dict],
          secrets: Iterable[str] = (), admission_seconds: Optional[float] = None) -> PruebaScore:
    s = PruebaScore(str(prueba.get("id", "?")))
    resumen = resumen or ""
    if admission_seconds is not None:
        s.add("POST /tasks < 2 s", admission_seconds < 2.0, f"{admission_seconds:.2f} s")
    if app_check is None:
        s.add("estado de la app (/__check)", False, "sin respuesta de /__check")
    else:
        for c in app_check.get("checks", []):
            s.add(f"app: {c.get('nombre', '?')}", bool(c.get("ok")))
        s.add("app: pass", bool(app_check.get("pass")))
    expected_state = prueba.get("estado_esperado")
    s.add("estado final", estado == expected_state, f"{estado!r} (esperado {expected_state!r})")
    line = answer_line(resumen)
    if prueba.get("respuesta_esperada") is not None and expected_state == "terminada":
        ok = line is not None and answer_matches(line, prueba["respuesta_esperada"])
        s.add("línea 'respuesta:'", ok, "presente" if line is not None else "ausente")
    elif expected_state == "fallida" and line is not None:
        s.add("sin línea 'respuesta:' en tarea fallida", False)
    for key in prueba.get("claves_razon", []) or []:
        s.add(f"resumen nombra {key}", str(key) in resumen)
    leaked = [x for x in secrets if x and x in resumen]
    s.add("resumen sin secretos", not leaked)
    return s
