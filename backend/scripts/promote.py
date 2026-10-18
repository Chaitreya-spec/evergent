"""Set an account's role.

    python -m scripts.promote you@example.com                 # admin
    python -m scripts.promote you@example.com --revoke        # back to member
    python -m scripts.promote someone@example.com --role viewer   # read-only

Admin routes — providers, agents, routing stats — are deliberately not
self-service. No API makes a user an admin, because whoever could call it
would control every user's agent roster and provider credentials. This needs
shell access to the server instead: the trust boundary that already holds the
database.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.db.session import SessionFactory
from app.models.entities import User
from app.models.enums import UserRole


async def main(email: str, *, revoke: bool = False, role: str | None = None) -> int:
    async with SessionFactory() as session:
        user = await session.scalar(select(User).where(User.email == email))
        if user is None:
            print(f"  no account for {email} — register it first", file=sys.stderr)
            return 1
        user.role = UserRole(role) if role else UserRole.MEMBER if revoke else UserRole.ADMIN
        await session.commit()

    print(f"  {email} is now {user.role.value}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Set an account's role.")
    parser.add_argument("email", help="the account's email, exactly as registered")
    parser.add_argument("--revoke", action="store_true", help="demote to member")
    parser.add_argument("--role", choices=[r.value for r in UserRole], help="set this role exactly")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.email, revoke=args.revoke, role=args.role)))
