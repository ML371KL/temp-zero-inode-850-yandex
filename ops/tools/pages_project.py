"""Проект Cloudflare Pages витрины — через API, а не `wrangler pages project create`
(та команда заводит Worker вместо проекта Pages).

    python ops/tools/pages_project.py check  [--name tzi-850-yandex]   есть ли проект (только чтение)
    python ops/tools/pages_project.py create [--name …] [--branch main] создать с производственной веткой
    python ops/tools/pages_project.py delete --name tzi-850-yandex --yes  откат создания: только этот проект
                                                                        и только без развёртываний

Токен: `CLOUDFLARE_API_TOKEN`, иначе OAuth-токен wrangler из его файла настроек
(`WRANGLER_CONFIG`, по умолчанию `%APPDATA%/xdg.config/.wrangler/config/default.toml`) — читается
внутри процесса и не печатается. OAuth живёт недолго: перед запуском — любая команда
`npx wrangler@4.135.0 …` (например, `whoami`), она его обновит. Аккаунт — единственный доступный
токену или `CLOUDFLARE_ACCOUNT_ID`. Печатаются только итог, имя, поддомен и производственная ветка —
ни токена, ни идентификатора аккаунта.

Коды: 0 — сделано (check: проект есть); 2 — check: проекта нет; 1 — отказ API или настроек.
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.cloudflare.com/client/v4"
OWN = "tzi-850-yandex"  # единственный проект, который delete вправе удалить
UA = "tzi-850-pages-project/1.0"


def token() -> str:
    if os.environ.get("CLOUDFLARE_API_TOKEN"):
        return os.environ["CLOUDFLARE_API_TOKEN"].strip()
    default = Path(os.environ.get("APPDATA", "")) / "xdg.config" / ".wrangler" / "config" / "default.toml"
    path = Path(os.environ.get("WRANGLER_CONFIG") or default)
    if not path.is_file():
        raise SystemExit("нет CLOUDFLARE_API_TOKEN и файла настроек wrangler — войдите `npx wrangler@4.135.0 login`")
    found = re.search(r'^oauth_token\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8"), re.M)
    if not found:
        raise SystemExit(f"в {path.name} нет oauth_token")
    return found.group(1)


def call(method: str, path: str, tok: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "authorization": f"Bearer {tok}", "content-type": "application/json", "user-agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except ValueError:
            return exc.code, {"success": False, "errors": [{"message": f"http {exc.code}"}]}


def errors(payload: dict) -> str:
    return "; ".join(f"{e.get('code', '')} {e.get('message', '')}".strip()
                     for e in payload.get("errors") or []) or "без описания"


def account(tok: str) -> str:
    if os.environ.get("CLOUDFLARE_ACCOUNT_ID"):
        return os.environ["CLOUDFLARE_ACCOUNT_ID"].strip()
    status, payload = call("GET", "/accounts", tok)
    accounts = payload.get("result") or []
    if status != 200 or not payload.get("success"):
        raise SystemExit(f"список аккаунтов: {status} {errors(payload)} — обновите вход: npx wrangler@4.135.0 whoami")
    if len(accounts) != 1:
        raise SystemExit(f"аккаунтов {len(accounts)} — задайте CLOUDFLARE_ACCOUNT_ID")
    return accounts[0]["id"]


def show(project: dict) -> str:
    return (f"{project.get('name')} · {project.get('subdomain')} · "
            f"производственная ветка {project.get('production_branch')}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Проект Cloudflare Pages витрины")
    parser.add_argument("command", choices=("check", "create", "delete"))
    parser.add_argument("--name", default="tzi-850-yandex")
    parser.add_argument("--branch", default="main", help="производственная ветка")
    parser.add_argument("--yes", action="store_true", help="delete: подтверждение")
    args = parser.parse_args(argv)
    if args.command == "delete" and args.name != OWN:  # до сети: опечатка не дойдёт до чужого проекта
        print(f"ОТКАЗ: delete удаляет только {OWN}, не {args.name}", file=sys.stderr)
        return 1
    tok = token()
    acc = account(tok)
    base = f"/accounts/{acc}/pages/projects"
    status, payload = call("GET", f"{base}/{args.name}", tok)
    exists = status == 200 and payload.get("success")
    if status not in (200, 404):
        print(f"ОТКАЗ: проверка проекта {args.name}: {status} {errors(payload)}", file=sys.stderr)
        return 1

    if args.command == "check":
        print(f"проект есть: {show(payload['result'])}" if exists else f"проекта {args.name} нет — имя свободно")
        return 0 if exists else 2

    if args.command == "create":
        if exists:
            print(f"ОТКАЗ: проект уже есть: {show(payload['result'])}", file=sys.stderr)
            return 1
        status, payload = call("POST", base, tok, {"name": args.name, "production_branch": args.branch})
        if status != 200 or not payload.get("success"):
            print(f"ОТКАЗ: создание {args.name}: {status} {errors(payload)}", file=sys.stderr)
            return 1
        result = payload["result"]
        if result.get("subdomain") != f"{args.name}.pages.dev":
            print(f"ВНИМАНИЕ: поддомен {result.get('subdomain')} — имя {args.name}.pages.dev занято", file=sys.stderr)
            return 1
        print(f"создан: {show(result)}")
        return 0

    if not args.yes:
        print("ОТКАЗ: delete без --yes", file=sys.stderr)
        return 1
    if not exists:
        print(f"проекта {args.name} нет — удалять нечего")
        return 0
    status, payload = call("GET", f"{base}/{args.name}/deployments", tok)
    if status != 200 or not payload.get("success"):
        print(f"ОТКАЗ: список развёртываний {args.name}: {status} {errors(payload)}", file=sys.stderr)
        return 1
    count = (payload.get("result_info") or {}).get("total_count") or len(payload.get("result") or [])
    if count:
        print(f"ОТКАЗ: у {args.name} развёртываний {count} — это не откат создания, удалять нельзя", file=sys.stderr)
        return 1
    status, payload = call("DELETE", f"{base}/{args.name}", tok)
    if status != 200 or not payload.get("success"):
        print(f"ОТКАЗ: удаление {args.name}: {status} {errors(payload)}", file=sys.stderr)
        return 1
    print(f"удалён: {args.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
