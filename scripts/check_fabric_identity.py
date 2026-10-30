"""Ejecuta las sondas administrativas en Azure; claves únicamente en memoria."""
import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import requests

SUB = "d468911c-54a0-4a0b-af8c-938d81aeea30"
RG = "rg-pi-epre-dev"
APP = "func-pi-epre-dev-hnso27"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("original", "alternate"), default="original")
    args = parser.parse_args()
    az = shutil.which("az")
    if not az:
        raise RuntimeError("Azure CLI no está disponible.")

    def azure(*command):
        result = subprocess.run([az, *command, "--subscription", SUB, "--resource-group", RG, "--output", "json"],
                                capture_output=True, text=True, timeout=90)
        if result.returncode:
            raise RuntimeError("No se pudieron obtener los metadatos administrativos; salida omitida.")
        return json.loads(result.stdout)

    hostname = azure("resource", "show", "--resource-type", "Microsoft.Web/sites", "--name", APP,
                     "--api-version", "2023-12-01", "--query", "properties.defaultHostName")
    if not hostname or not hostname.endswith(".azurewebsites.net"):
        raise RuntimeError("Hostname inválido.")
    keys = azure("functionapp", "keys", "list", "--name", APP)
    master = keys.get("masterKey")
    regular = keys.get("functionKeys", {}).get("default")
    if not master or not regular:
        raise RuntimeError("No se pudieron obtener las claves necesarias; valores omitidos.")
    url = f"https://{hostname}/api/internal/diagnostics/data-access"
    checks = []
    for case, headers in [("anonymous", {}), ("regular_function_key", {"x-functions-key": regular})]:
        response = requests.post(url, headers=headers, timeout=(10, 120), allow_redirects=False)
        checks.append({"case": case, "http_status": response.status_code, "blocked": response.status_code == 401})
        print(json.dumps(checks[-1]))
        if response.status_code != 401:
            raise RuntimeError("La ruta administrativa no rechazó el acceso de menor privilegio.")
    trace_id = uuid4().hex
    response = requests.post(url, headers={"x-functions-key": master,
        "traceparent": f"00-{trace_id}-{uuid4().hex[:16]}-01"}, timeout=(10, 120), allow_redirects=False)
    report = {"verified_at": datetime.now(timezone.utc).isoformat(), "target": args.target, "authorization_checks": checks,
              "diagnostic_http_status": response.status_code,
              "trace_id": response.headers.get("X-Trace-ID"), "request_id": response.headers.get("X-Request-ID")}
    payload = response.json() if response.status_code == 200 else {}
    if payload.get("diagnostic_version") == "2026-10-29.3":
        report["diagnostic"] = payload
    else:
        report["diagnostic_available"] = False
    destination = Path(__file__).resolve().parents[1] / "dist" / "fabric-identity-diagnostic.json"
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.target == "alternate":
        destination.with_name("fabric-alternate-identity-diagnostic.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if args.target == "alternate":
        return 0 if any(p.get("probe") == "alternate_kpi_first10" and p.get("read_succeeded")
                        for p in report.get("diagnostic", {}).get("comparison_probes", [])) else 1
    return 0 if report.get("diagnostic", {}).get("data_read_succeeded") else 1


if __name__ == "__main__":
    raise SystemExit(main())
