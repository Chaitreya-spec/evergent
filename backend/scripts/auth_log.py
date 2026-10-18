"""Show the security log: sign-ups, sign-ins, failed sign-ins, sign-outs and more.

    .venv/bin/python -m scripts.auth_log              # last 50 sign-in events
    .venv/bin/python -m scripts.auth_log --all        # every audited action
    .venv/bin/python -m scripts.auth_log --follow     # keep printing new ones
    .venv/bin/python -m scripts.auth_log --email you@example.com

Reads the audit_logs table. Passwords are never in it.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
from datetime import datetime

from sqlalchemy import or_, select

from app.db.session import SessionFactory
from app.models.entities import AuditLog, User

COLOURS = {
    "auth.login": "\033[32m",
    "auth.register": "\033[36m",
    "auth.logout": "\033[33m",
    "auth.login_failed": "\033[31m",
    "auth.refresh_reused": "\033[31m",
}
RESET = "\033[0m"


def _line(entry: AuditLog, email: str | None) -> str:
    when = entry.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    who = email or (entry.meta or {}).get("email") or "-"
    browser = (entry.user_agent or "-")[:48]
    colour = COLOURS.get(entry.action, "")
    return f"{when}  {colour}{entry.action:<28}{RESET}  {who:<32}  {entry.ip or '-':<16}  {browser}"


async def _fetch(*, everything: bool, email: str | None, after: datetime | None, limit: int):
    query = select(AuditLog, User.email).outerjoin(User, User.id == AuditLog.user_id)
    if not everything:
        query = query.where(AuditLog.action.like("auth.%"))
    if email:
        query = query.where(or_(User.email == email, AuditLog.meta["email"].as_string() == email))
    if after is not None:
        query = query.where(AuditLog.created_at > after).order_by(AuditLog.created_at)
    else:
        query = query.order_by(AuditLog.created_at.desc()).limit(limit)
    async with SessionFactory() as session:
        rows = list((await session.execute(query)).all())
    return rows if after is not None else list(reversed(rows))


async def main(*, everything: bool, follow: bool, email: str | None, limit: int) -> None:
    print(f"{'WHEN':<19}  {'ACTION':<28}  {'EMAIL':<32}  {'IP':<16}  BROWSER")
    rows = await _fetch(everything=everything, email=email, after=None, limit=limit)
    for entry, address in rows:
        print(_line(entry, address))
    if not follow:
        return
    last = rows[-1][0].created_at if rows else datetime.now().astimezone()
    print("-- following; Ctrl+C to stop --")
    while True:
        await asyncio.sleep(2)
        for entry, address in await _fetch(everything=everything, email=email, after=last, limit=limit):
            print(_line(entry, address), flush=True)
            last = entry.created_at


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Show the security log.")
    parser.add_argument("--all", action="store_true", help="every audited action, not just sign-in events")
    parser.add_argument("--follow", "-f", action="store_true", help="keep printing new events")
    parser.add_argument("--email", help="only this account")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main(everything=args.all, follow=args.follow, email=args.email, limit=args.limit))
