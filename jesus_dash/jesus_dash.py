import asyncio
import importlib
import logging
import os
from pathlib import Path

from redbot.core import Config, commands


DASH_CHID = int(os.getenv("DASH_CHID", "0"))

RELOAD = "\U0001F504"
UPDATE = "\u2B06\uFE0F"
REFRESH = "\u2139\uFE0F"

log = logging.getLogger("red.jesus_dash")

DASHBOARD_TEXT = f"""Jesus Dashboard

{RELOAD} Reload Jesus and Jesus Dash
{UPDATE} Pull the latest cog code, then reload
{REFRESH} Refresh this dashboard

Only Redbot owners can use these controls."""


class JesusDash(commands.Cog):
    """Reaction-based controls for the Jesus cogs."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(
            self,
            identifier=784361923,
            force_registration=True,
        )
        self.config.register_global(message_id=None)
        self._startup_task = None

    async def cog_load(self):
        self._startup_task = asyncio.create_task(self.ensure_dashboard())

    def cog_unload(self):
        if self._startup_task:
            self._startup_task.cancel()

    async def ensure_dashboard(self):
        await self.bot.wait_until_ready()

        if not DASH_CHID:
            log.warning("DASH_CHID is not configured; dashboard is disabled")
            return

        channel = self.bot.get_channel(DASH_CHID)
        if channel is None:
            channel = await self.bot.fetch_channel(DASH_CHID)

        message = None
        message_id = await self.config.message_id()

        if message_id:
            try:
                message = await channel.fetch_message(message_id)
            except Exception:
                pass

        if message is None:
            message = await channel.send(DASHBOARD_TEXT)
            await self.config.message_id.set(message.id)
        else:
            await message.edit(content=DASHBOARD_TEXT)

        for emoji in (RELOAD, UPDATE, REFRESH):
            await message.add_reaction(emoji)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if payload.user_id == self.bot.user.id:
            return

        if payload.channel_id != DASH_CHID:
            return

        if payload.message_id != await self.config.message_id():
            return

        if payload.user_id not in self.bot.owner_ids:
            return

        emoji = str(payload.emoji)

        if emoji == RELOAD:
            await self.run_reload("Reloaded Jesus cogs.")
        elif emoji == UPDATE:
            await self.run_update()
        elif emoji == REFRESH:
            await self.ensure_dashboard()

    async def run_reload(self, status):
        extensions = [
            name
            for name in self.bot.extensions
            if name.rsplit(".", 1)[-1] in {"jesus", "jesus_dash"}
        ]

        if not extensions:
            await self.set_status("No loaded Jesus extensions were found.")
            return

        reloaded = []

        for extension in extensions:
            try:
                self.bot.unload_extension(extension)
                importlib.invalidate_caches()
                spec = importlib.util.find_spec(extension)

                if spec is None:
                    raise RuntimeError("module specification was not found")

                await self.bot.load_extension(spec)
                reloaded.append(extension)
            except Exception:
                log.exception("Failed to reload extension %s", extension)

        if reloaded:
            await self.set_status(status)
        else:
            await self.set_status("Jesus cog reload failed; check the Redbot log.")

    async def run_update(self):
        repo_root = self.find_repo_root()

        if repo_root is None:
            await self.set_status(
                "No Git checkout was found. Update the cogs through CogManager."
            )
            return

        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(repo_root),
            "pull",
            "--ff-only",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode:
            details = (stderr or stdout).decode().strip()[:300]
            await self.set_status(f"Update failed: {details or 'git pull failed'}")
            return

        await self.run_reload("Updated from Git and reloaded Jesus cogs.")

    def find_repo_root(self):
        for path in Path(__file__).resolve().parents:
            if (path / ".git").exists():
                return path
        return None

    async def set_status(self, status):
        if not DASH_CHID:
            return

        channel = self.bot.get_channel(DASH_CHID)
        if channel is None:
            channel = await self.bot.fetch_channel(DASH_CHID)

        message_id = await self.config.message_id()

        try:
            message = await channel.fetch_message(message_id)
        except Exception:
            await self.ensure_dashboard()
            return

        await message.edit(content=f"{DASHBOARD_TEXT}\n\nLast action: {status}")
