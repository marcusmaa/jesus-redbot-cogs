import asyncio
import logging
import os
import re
import shutil
import tempfile
import uuid

import aiohttp
try:
    import imageio_ffmpeg
except ImportError:
    imageio_ffmpeg = None

from redbot.core import commands


CHANNEL_ID = 1541859651782451329

HOURGLASS = "\u23F3"
SUCCESS = "\u2705"
FAILED = "\u274C"

log = logging.getLogger("red.jesus")

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
            video_path = await self.strip_metadata(video_path)
            share_url = await self.upload_to_loops(video_path)

            # Sending the canonical URL makes Discord render Loops' video embed.
            await message.channel.send(share_url)

            # Do not delete the source message until upload and share-link post succeed.
            await message.delete()
            log.info("Uploaded to Loops and posted share link: %s", share_url)

        except Exception as e:
            log.exception("Video archive failed for %s", url)

            try:
                await message.remove_reaction(HOURGLASS, self.bot.user)
                await message.add_reaction(FAILED)
            except Exception:
                pass

        finally:
            if video_path:
                shutil.rmtree(os.path.dirname(video_path), ignore_errors=True)

    async def download_video(self, url):
        yt_dlp = shutil.which("yt-dlp")

        if not yt_dlp:
            raise RuntimeError("yt-dlp is not installed or is not on PATH")

        download_dir = tempfile.mkdtemp(prefix="jesus_")

        output = os.path.join(
            download_dir,
            "%(id)s.%(ext)s"
        )

        process = await asyncio.create_subprocess_exec(
            yt_dlp,
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

    async def strip_metadata(self, video_path):
        ffmpeg_candidates = [
            os.getenv("FFMPEG_PATH"),
            shutil.which("ffmpeg"),
            "/usr/bin/ffmpeg",
            "/usr/local/bin/ffmpeg",
            "/bin/ffmpeg",
            "/data/venv/bin/ffmpeg",
        ]

        if imageio_ffmpeg:
            try:
                ffmpeg_candidates.append(imageio_ffmpeg.get_ffmpeg_exe())
            except RuntimeError:
                pass
        ffmpeg = next(
            (
                candidate
                for candidate in ffmpeg_candidates
                if candidate
                and os.path.isfile(candidate)
                and os.access(candidate, os.X_OK)
            ),
            None,
        )

        if not ffmpeg:
            raise RuntimeError(
                "ffmpeg was not found; set FFMPEG_PATH to its executable path"
            )

        clean_path = os.path.join(
            os.path.dirname(video_path),
            f"{uuid.uuid4().hex}.mp4",
        )
        process = await asyncio.create_subprocess_exec(
            ffmpeg,
            "-y",
            "-i",
            video_path,
            "-map",
            "0:v?",
            "-map",
            "0:a?",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-c",
            "copy",
            clean_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()

        if process.returncode != 0:
            raise RuntimeError(
                f"ffmpeg metadata cleanup failed: {stderr.decode().strip()}"
            )

        if not os.path.exists(clean_path):
            raise RuntimeError("ffmpeg did not create the cleaned video")

        os.remove(video_path)
        return clean_path

    async def upload_to_loops(self, video_path):
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
        # Used only to locate the asynchronous upload; cleared before sharing.
        upload_marker = uuid.uuid4().hex

        form = aiohttp.FormData()

        with open(video_path, "rb") as video_file:
            form.add_field(
                "video",
                video_file,
                filename=os.path.basename(video_path),
                content_type="video/mp4",
            )
            form.add_field("description", upload_marker)

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

                    await response.read()

                video = await self.wait_for_loops_share_url(
                    session,
                    api_base,
                    headers,
                    upload_marker,
                )
                await self.clear_loops_caption(
                    session,
                    api_base,
                    headers,
                    video["id"],
                )
                return video["url"]

    async def wait_for_loops_share_url(
        self,
        session,
        api_base,
        headers,
        upload_marker,
    ):
        posts_url = (
            f"{api_base}/studio/posts"
            "?limit=20&sort_field=created_at&sort_direction=desc"
        )

        # Loops processes uploads asynchronously, so wait for this exact post to
        # become published and expose its canonical share URL.
        for _ in range(60):
            async with session.get(posts_url, headers=headers) as response:
                if not 200 <= response.status < 300:
                    details = (await response.text())[:500]
                    raise RuntimeError(
                        f"Loops post lookup failed ({response.status}): {details}"
                    )

                payload = await response.json(content_type=None)

            for video in payload.get("data", []):
                if not isinstance(video, dict):
                    continue

                if upload_marker not in (video.get("caption") or ""):
                    continue

                share_url = video.get("url")

                if (
                    video.get("status") == "published"
                    and isinstance(share_url, str)
                    and share_url.startswith(("https://", "http://"))
                ):
                    return {"id": video["id"], "url": share_url}

            await asyncio.sleep(5)

        raise RuntimeError(
            "Loops did not publish the uploaded video within five minutes"
        )

    async def clear_loops_caption(self, session, api_base, headers, video_id):
        edit_url = f"{api_base}/video/edit/{video_id}"

        async with session.post(
            edit_url,
            json={"caption": ""},
            headers=headers,
        ) as response:
            if not 200 <= response.status < 300:
                details = (await response.text())[:500]
                raise RuntimeError(
                    f"Loops caption cleanup failed ({response.status}): {details}"
                )
