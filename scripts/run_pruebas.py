#!/usr/bin/env python3
"""Ejecuta las pruebas de práctica (frontier-reto-1/pruebas) contra un escritorio real.

Reproduce la evaluación oficial por cada prueba:
  1. ``POST /__reset`` del escritorio;
  2. ``POST /tasks`` al agente con el cuerpo del evaluador (``tarea`` + ``escritorio``);
  3. sondeo de ``GET /tasks/{id}`` cada 5 s hasta ``terminada``/``fallida`` (máx. 5 min);
  4. ``POST /__check`` con el JSON de la prueba y puntuación de estado y resumen.

Credenciales por entorno o en ``.env.pruebas`` (ignorado por git; nunca se imprimen):
  DESKTOP_BASE_URL, DESKTOP_PASSWORD, DESKTOP_ADMIN_TOKEN, TEAM_TOKEN

Uso:
  python scripts/run_pruebas.py                     # todas, contra http://localhost:8000
  python scripts/run_pruebas.py --only E1 E3 --agent-url https://mi-agente.up.railway.app
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from web_agent.evaluation import score  # noqa: E402

POLL_SECONDS = 5
TASK_LIMIT_SECONDS = 300


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"Falta la variable {name} (defínela o crea .env.pruebas).")
    return value


def run_one(client: httpx.Client, prueba: dict, *, agent: str, desktop: str, ws_url: str,
            password: str, admin_token: str, team_token: str) -> dict:
    admin = {"Authorization": f"Bearer {admin_token}"}
    team = {"Authorization": f"Bearer {team_token}"}
    r = client.post(f"{desktop}/__reset", headers=admin, timeout=60)
    r.raise_for_status()
    time.sleep(5)

    body = {"tarea": prueba["instruccion"], "escritorio": {"url": ws_url, "password": password}}
    started = time.monotonic()
    r = client.post(f"{agent}/tasks", headers=team, json=body, timeout=10)
    admission = time.monotonic() - started
    r.raise_for_status()
    task_id = r.json()["task_id"]

    estado, resumen = "en_curso", ""
    while time.monotonic() - started < TASK_LIMIT_SECONDS:
        time.sleep(POLL_SECONDS)
        try:
            data = client.get(f"{agent}/tasks/{task_id}", headers=team, timeout=10).json()
        except (httpx.HTTPError, ValueError):
            continue
        estado, resumen = data.get("estado"), data.get("resumen", "")
        if estado in ("terminada", "fallida"):
            break
    elapsed = time.monotonic() - started

    try:
        app_check = client.post(f"{desktop}/__check", headers=admin, json=prueba, timeout=60).json()
    except (httpx.HTTPError, ValueError):
        app_check = None
    result = score(prueba, estado=estado, resumen=resumen, app_check=app_check,
                   secrets=(password, admin_token, team_token), admission_seconds=admission)
    return {"id": prueba["id"], "nivel": prueba.get("nivel"), "pass": result.passed, "estado": estado,
            "elapsed_s": round(elapsed, 1), "resumen": resumen.replace(password, "[SECRETO]"),
            "checks": [{"nombre": c.name, "ok": c.ok, "detalle": c.detail} for c in result.checks]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agent-url", default="http://localhost:8000")
    ap.add_argument("--pruebas", default=str(ROOT / "frontier-reto-1" / "pruebas"))
    ap.add_argument("--only", nargs="*", help="IDs a ejecutar (p.ej. E1 E3)")
    ap.add_argument("--report", default=str(ROOT / "data" / "pruebas" / "report.json"))
    args = ap.parse_args()

    load_env(ROOT / ".env.pruebas")
    desktop = require("DESKTOP_BASE_URL").rstrip("/")
    password, admin_token, team_token = require("DESKTOP_PASSWORD"), require("DESKTOP_ADMIN_TOKEN"), require("TEAM_TOKEN")
    ws_url = desktop.replace("https://", "wss://").replace("http://", "ws://") + "/websockify"

    files = sorted(Path(args.pruebas).glob("*.json"))
    pruebas = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    if args.only:
        pruebas = [p for p in pruebas if p["id"] in set(args.only)]
    results = []
    with httpx.Client() as client:
        for prueba in pruebas:
            print(f"[{prueba['id']}] {prueba['instruccion']}", flush=True)
            res = run_one(client, prueba, agent=args.agent_url.rstrip("/"), desktop=desktop, ws_url=ws_url,
                          password=password, admin_token=admin_token, team_token=team_token)
            results.append(res)
            mark = "PASA" if res["pass"] else "FALLA"
            print(f"   {mark} · estado={res['estado']} · {res['elapsed_s']} s")
            for c in res["checks"]:
                print(f"     {'✔' if c['ok'] else '✘'} {c['nombre']}" + (f" ({c['detalle']})" if c["detalle"] else ""))
            print(f"     resumen: {res['resumen'][:300]}")
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    passed = sum(r["pass"] for r in results)
    print(f"\n{passed}/{len(results)} pruebas correctas · informe: {report}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
