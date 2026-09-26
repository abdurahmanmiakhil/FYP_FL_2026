"""Generate docs/api.md from the FastAPI OpenAPI schema:  python scripts/gen_api_docs.py"""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("ENV", "dev")
os.environ.setdefault("LOG_LEVEL", "WARNING")

from backend.main import create_app  # noqa: E402

ROLE_NOTES = {
    "users (admin)": "Admin only.", "admin": "Admin only, except `GET /admin/model` (all signed-in users).",
}


def main() -> None:
    spec = create_app().openapi()
    out = ["# GleasonAI API reference", "",
           "Generated from the OpenAPI schema (`python scripts/gen_api_docs.py`). Interactive docs: "
           "`/api/v1/docs` in development (disabled in production).", "",
           "**Authentication:** `POST /api/v1/auth/login` sets httpOnly cookies (`access_token` 15 min, "
           "`refresh_token` 7 days, rotating) and a readable `csrf_token` cookie. Every state-changing request "
           "(POST/PUT/PATCH/DELETE, except login) must send the same value in the `X-CSRF-Token` header. "
           "Non-admin users only see cases of their own hospital (other cases answer 404).", "",
           "Example with curl:", "", "```bash",
           "curl -sk -c jar -b jar -H 'Content-Type: application/json' \\",
           "  -d '{\"email\":\"you@hospital.org\",\"password\":\"...\"}' https://localhost/api/v1/auth/login",
           "CSRF=$(awk '/csrf_token/ {print $7}' jar)",
           "curl -sk -c jar -b jar -H \"X-CSRF-Token: $CSRF\" -F file=@slide.tiff -F pseudonym_code=PT-0001 \\",
           "  https://localhost/api/v1/cases", "```", ""]
    groups: dict[str, list[tuple[str, str, dict]]] = {}
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            groups.setdefault(op.get("tags", ["other"])[0], []).append((method.upper(), path, op))
    for tag, items in groups.items():
        out += [f"## {tag}", ""]
        if tag in ROLE_NOTES:
            out += [ROLE_NOTES[tag], ""]
        out += ["| Method | Path | Summary |", "|---|---|---|"]
        for method, path, op in items:
            out.append(f"| `{method}` | `{path}` | {op.get('summary', '')} |")
        out.append("")
    out += ["## Schemas", ""]
    for name, schema in spec["components"]["schemas"].items():
        if name.startswith(("HTTPValidationError", "ValidationError", "Body_")):
            continue
        props = schema.get("properties", {})
        req = set(schema.get("required", []))
        out += [f"### {name}", "", "| Field | Type | Required |", "|---|---|---|"]
        for field, p in props.items():
            t = p.get("type") or p.get("$ref", "").split("/")[-1] or " | ".join(
                x.get("type", x.get("$ref", "").split("/")[-1]) for x in p.get("anyOf", []))
            out.append(f"| `{field}` | {t} | {'yes' if field in req else ''} |")
        out.append("")
    Path("docs/api.md").write_text("\n".join(out))
    Path("docs/openapi.json").write_text(json.dumps(spec, indent=2))
    print(f"docs/api.md: {sum(len(v) for v in groups.values())} operations, {len(spec['components']['schemas'])} schemas")


if __name__ == "__main__":
    main()
