import asyncio
import importlib
import logging
import os
import sys
from importlib.util import find_spec
from pathlib import Path

from discord.ext import tasks
from redbot.core import Config, commands


DASH_CHID = int(os.getenv("DASH_CHID", "0"))

RELOAD = "\U0001F504"
UPDATE = "\u2B06\uFE0F"
REFRESH = "\u2139\uFE0F"

log = logging.getLogger("red.jesus_dash")


class JesusDash(commands.Cog):
    """Reaction-based dashboard for all installed cogs."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(
            self,
            identifier=784361923,
            force_registration=True,
        )
        self.config.register_global(
            message_id=None,
            last_status="Ready.",
        )
        self._startup_task = None

    async def cog_load(self):
        self._startup_task = asyncio.create_task(self.ensure_dashboard())
        self.prompt_refresh_loop.start()

    def cog_unload(self):
        if self._startup_task:
            self._startup_task.cancel()

        self.prompt_refresh_loop.cancel()

    def managed_extensions(self):
        return sorted(
            name
            for name in self.bot.extensions
            if not name.startswith("redbot.")
        )

    async def dashboard_text(self):
        status = await self.config.last_status()
        cog_count = len(self.managed_extensions())

        return (
            "Cog Dashboard\n\n"
            f"{RELOAD} Reload all loaded cogs\n"
            f"{UPDATE} Check all Git-backed cog checkouts for updates, then reload\n"
            f"{REFRESH} Refresh this dashboard\n\n"
            f"Loaded user cogs: {cog_count}\n"
            f"Last action: {status}\n\n"
            "Only Redbot owners can use these controls."
        )

    async def get_dashboard_channel(self):
        if not DASH_CHID:
            return None

        channel = self.bot.get_channel(DASH_CHID)

        if channel is None:
            channel = await self.bot.fetch_channel(DASH_CHID)

        return channel

    async def ensure_dashboard(self):
        await self.bot.wait_until_ready()

        channel = await self.get_dashboard_channel()

        if channel is None:
            log.warning("DASH_CHID is not configured; dashboard is disabled")
            return

        message = None
        message_id = await self.config.message_id()

        if message_id:
            try:
                message = await channel.fetch_message(message_id)
            except Exception:
                pass

        content = await self.dashboard_text()

        if message is None:
            message = await channel.send(content)
            await self.config.message_id.set(message.id)
        else:
            await message.edit(content=content)

        for emoji in (RELOAD, UPDATE, REFRESH):
            await message.add_reaction(emoji)

    @tasks.loop(minutes=1)
    async def prompt_refresh_loop(self):
        await self.ensure_dashboard()

    @prompt_refresh_loop.before_loop
    async def before_prompt_refresh_loop(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if self.bot.user and payload.user_id == self.bot.user.id:
            return

        if payload.channel_id != DASH_CHID:
            return

        if payload.message_id != await self.config.message_id():
            return

        if payload.user_id not in self.bot.owner_ids:
            return

        emoji = str(payload.emoji)

        if emoji == RELOAD:
            reloaded, failed = await self.reload_all_cogs()
            await self.set_status(
                f"Reloaded {reloaded} cog(s); {failed} failed."
            )
        elif emoji == UPDATE:
            await self.update_all_cogs()
        elif emoji == REFRESH:
            await self.set_status("Dashboard refreshed.")

    async def reload_all_cogs(self):
        extensions = self.managed_extensions()
        reloaded = 0
        failed = 0

        # Reload this dashboard last so it remains available to report results.
        extensions.sort(key=lambda name: name.rsplit(".", 1)[-1] == "jesus_dash")

        for extension in extensions:
            try:
                self.bot.unload_extension(extension)
                importlib.invalidate_caches()
                spec = find_spec(extension)

                if spec is None:
                    raise RuntimeError("module specification was not found")

                await self.bot.load_extension(spec)
                reloaded += 1
            except Exception:
                failed += 1
                log.exception("Failed to reload extension %s", extension)

        return reloaded, failed

    def git_roots_for_cogs(self):
        roots = set()

        for extension in self.managed_extensions():
            module = sys.modules.get(extension)
            module_path = getattr(module, "__file__", None)

            if not module_path:
                continue

            for parent in Path(module_path).resolve().parents:
                if (parent / ".git").exists():
                    roots.add(parent)
                    break

        return sorted(roots)

    async def update_all_cogs(self):
        roots = self.git_roots_for_cogs()

        if not roots:
            await self.set_status(
                "No Git-backed cog checkouts found; use CogManager to update."
            )
            return

        updated = 0
        failed = 0

        for root in roots:
            process = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(root),
                "pull",
                "--ff-only",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            if process.returncode:
                failed += 1
                details = (stderr or stdout).decode().strip()[:300]
                log.error("Cog update failed in %s: %s", root, details)
            else:
                updated += 1

        reloaded, reload_failed = await self.reload_all_cogs()
        failed += reload_failed
        await self.set_status(
            f"Checked {len(roots)} checkout(s), updated {updated}; "
            f"reloaded {reloaded} cog(s), {failed} failed."
        )

    async def set_status(self, status):
        await self.config.last_status.set(status)
        await self.ensure_dashboard()
