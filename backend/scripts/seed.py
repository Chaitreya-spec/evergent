"""Seed the catalog. Usage: python -m scripts.seed"""

from __future__ import annotations

import asyncio

from app.db.seed import seed_catalog
from app.db.session import SessionFactory


async def main() -> None:
    async with SessionFactory() as session:
        providers, models = await seed_catalog(session)
    print(f"  seeded {providers} provider(s), {models} model(s)")


if __name__ == "__main__":
    asyncio.run(main())
