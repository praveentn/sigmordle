"""
External leaderboard integration.

Configure via environment variables:
  EXTERNAL_LEADERBOARDS=SIGMAFEUD,NAVI        comma-separated list of service names
  SIGMAFEUD_ENABLED=true                      enable/disable each service
  SIGMAFEUD_URL=https://...                   base URL for the service
  SIGMAFEUD_API_KEY=your_key_here             API key for the service
  SIGMAFEUD_GUILDS=123456789,987654321        optional: only forward points from these guild IDs
                                              omit (or leave blank) to forward from all guilds
  NAVI_ENABLED=false
  NAVI_URL=https://...
  NAVI_API_KEY=your_key_here
  NAVI_GUILDS=123456789

Adding a new service requires only env var changes — no code changes needed.
"""

import os
import logging
from dataclasses import dataclass, field

import aiohttp

log = logging.getLogger("sigmordle.external_leaderboard")


@dataclass
class _ServiceConfig:
    name: str
    url: str
    api_key: str
    guild_allowlist: set[int] = field(default_factory=set)  # empty = all guilds

    def allows_guild(self, guild_id: int) -> bool:
        return not self.guild_allowlist or guild_id in self.guild_allowlist


def _load_enabled_services() -> list[_ServiceConfig]:
    raw = os.getenv("EXTERNAL_LEADERBOARDS", "").strip()
    if not raw:
        return []

    services: list[_ServiceConfig] = []
    for name in (s.strip().upper() for s in raw.split(",") if s.strip()):
        enabled = os.getenv(f"{name}_ENABLED", "false").strip().lower()
        if enabled not in ("1", "true", "yes"):
            continue
        url = os.getenv(f"{name}_URL", "").strip()
        api_key = os.getenv(f"{name}_API_KEY", "").strip()
        if not url or not api_key:
            log.warning("External leaderboard %s is enabled but missing URL or API_KEY — skipping.", name)
            continue
        raw_guilds = os.getenv(f"{name}_GUILDS", "").strip()
        guild_allowlist: set[int] = set()
        for g in raw_guilds.split(","):
            g = g.strip()
            if g.isdigit():
                guild_allowlist.add(int(g))
        services.append(_ServiceConfig(name=name, url=url, api_key=api_key, guild_allowlist=guild_allowlist))

    return services


async def post_points(
    user_id: int,
    guild_id: int,
    username: str,
    points: int,
    match_id: str | None = None,
) -> None:
    """Post a points award to all enabled external leaderboard services."""
    services = _load_enabled_services()
    if not services:
        return

    payload = {
        "user_id":  user_id,
        "guild_id": guild_id,
        "username": username,
        "points":   points,
        "game_id":  match_id,
    }

    async with aiohttp.ClientSession() as session:
        for svc in services:
            if not svc.allows_guild(guild_id):
                log.debug("External leaderboard %s: skipping guild %d (not in allowlist)", svc.name, guild_id)
                continue
            headers = {"Authorization": f"Bearer {svc.api_key}"}
            endpoint = f"{svc.url.rstrip('/')}/api/v1/points"
            try:
                async with session.post(endpoint, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json()
                    log.info("External leaderboard %s: user=%s points=%d response=%s", svc.name, user_id, points, data)
            except Exception as exc:
                log.warning("External leaderboard %s failed: %s", svc.name, exc)
