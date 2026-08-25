import asyncio
import os
import re
import tempfile

from redbot.core import commands


CHANNEL_ID = 1541859651782451329

SUPPORTED_URL = re.compile(
    r"https?://(?:www\.)?"
    r"(?:tiktok\.com|vm\.tiktok\.com|instagram\.com|youtube\.com|youtu\.be)"
    r"/\S+",
    re.IGNORECASE,
)


class Jesus(commands.Cog):
    """Downloads short-form videos posted in the monitored channel."""

    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        if message.channel.id != CHANNEL_ID:
            return

        match = SUPPORTED_URL.search(message.content)

        if not match:
            return

        url = match.group(0)

        await message.add_reaction("?")

        try:
            video_path = await self.download_video(url)

            print(f"Downloaded: {video_path}")

            await message.remove_reaction("?", self.bot.user)
            await message.add_reaction("?")

        except Exception as e:
            print(f"Download failed: {e}")

            await message.remove_reaction("?", self.bot.user)
            await message.add_reaction("?")

    async def download_video(self, url):
        download_dir = tempfile.mkdtemp(prefix="jesus_")

        output = os.path.join(download_dir, "%(id)s.%(ext)s")

        process = await asyncio.create_subprocess_exec(
            "/data/venv/bin/yt-dlp",
            "--no-playlist",
            "--format",
            "bv*+ba/b",
            "--merge-output-format",
            "mp4",
            "--output",
            output,
            "--print",
            "after_move:filepath",
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            raise RuntimeError(stderr.decode())

        return stdout.decode().strip()