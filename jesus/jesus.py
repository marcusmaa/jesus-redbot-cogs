import asyncio
import os
import re
import shutil
import tempfile

import aiohttp
from redbot.core import commands


CHANNEL_ID = 1541859651782451329

HOURGLASS = "\u23F3"
SUCCESS = "\u2705"
FAILED = "\u274C"

SUPPORTED_URL = re.compile(
    r"https?://(?:www\.)?"
    r"(?:tiktok\.com|vm\.tiktok\.com|instagram\.com|youtube\.com|youtu\.be)"
    r"/\S+",
    re.IGNORECASE,
)


class Jesus(commands.Cog):
    """Monitor short-form video links and download them."""

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
        video_path = None

        await message.add_reaction(HOURGLASS)

        try:
            video_path = await self.download_video(url)
            await self.upload_to_loops(video_path, url)

            # Do not delete the source message until Loops confirms the upload.
            await message.delete()
            print(f"Uploaded to Loops and deleted source message: {url}")

        except Exception as e:
            print(f"Video archive failed: {e}")

            try:
                await message.remove_reaction(HOURGLASS, self.bot.user)
                await message.add_reaction(FAILED)
            except Exception:
                pass

        finally:
            if video_path:
                shutil.rmtree(os.path.dirname(video_path), ignore_errors=True)

    async def download_video(self, url):
        download_dir = tempfile.mkdtemp(prefix="jesus_")

        output = os.path.join(
            download_dir,
            "%(id)s.%(ext)s"
        )

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
            shutil.rmtree(download_dir, ignore_errors=True)
            raise RuntimeError(stderr.decode().strip())

        video_path = stdout.decode().strip()

        if not video_path:
            shutil.rmtree(download_dir, ignore_errors=True)
            raise RuntimeError("yt-dlp returned no output path")

        if not os.path.exists(video_path):
            shutil.rmtree(download_dir, ignore_errors=True)
            raise RuntimeError(
                f"Downloaded file does not exist: {video_path}"
            )

        return video_path

    async def upload_to_loops(self, video_path, source_url):
        loops_url = os.getenv("LOOPS_URL", "").rstrip("/")
        access_token = os.getenv("LOOPS_ACCESS_TOKEN", "").strip()

        if not loops_url:
            raise RuntimeError("LOOPS_URL is not configured")

        if not access_token:
            raise RuntimeError("LOOPS_ACCESS_TOKEN is not configured")

        api_base = (
            loops_url
            if loops_url.endswith("/api/v1")
            else f"{loops_url}/api/v1"
        )
        upload_url = f"{api_base}/studio/upload"
        description = f"Archived from Discord: {source_url}"[:200]

        form = aiohttp.FormData()

        with open(video_path, "rb") as video_file:
            form.add_field(
                "video",
                video_file,
                filename=os.path.basename(video_path),
                content_type="video/mp4",
            )
            form.add_field("description", description)

            timeout = aiohttp.ClientTimeout(total=600)
            headers = {"Authorization": f"Bearer {access_token}"}

            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    upload_url,
                    data=form,
                    headers=headers,
                ) as response:
                    if not 200 <= response.status < 300:
                        details = (await response.text())[:500]
                        raise RuntimeError(
                            f"Loops upload failed ({response.status}): {details}"
                        )
