"""yt-dlp helpers: metadata fetch + download control."""

from __future__ import annotations

import os
import re
import shutil
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlparse

import yt_dlp


ProgressCallback = Callable[[dict[str, Any]], None]
StatusCallback = Callable[[str], None]


@dataclass
class MediaItem:
    id: str
    title: str
    url: str
    duration: Optional[int] = None
    channel: str = ""
    thumbnail: str = ""
    selected: bool = True


@dataclass
class MediaInfo:
    kind: str  # video | playlist
    title: str
    url: str
    channel: str = ""
    duration: Optional[int] = None
    thumbnail: str = ""
    description: str = ""
    view_count: Optional[int] = None
    webpage_url: str = ""
    is_mix: bool = False
    formats: list[dict[str, Any]] = field(default_factory=list)
    entries: list[MediaItem] = field(default_factory=list)


def get_ffmpeg_path() -> Optional[str]:
    """System ffmpeg, or bundled imageio-ffmpeg binary."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        path = imageio_ffmpeg.get_ffmpeg_exe()
        if path and os.path.exists(path):
            return path
    except Exception:
        pass
    return None


def has_ffmpeg() -> bool:
    return get_ffmpeg_path() is not None


def is_youtube_url(url: str) -> bool:
    url = (url or "").strip()
    if not url:
        return False
    return bool(
        re.search(
            r"(https?://)?(www\.)?(youtube\.com|youtu\.be|music\.youtube\.com)/",
            url,
            re.I,
        )
    )


def _playlist_id(url: str) -> str:
    try:
        qs = parse_qs(urlparse(url).query)
        return (qs.get("list") or [""])[0]
    except Exception:
        return ""


def is_mix_url(url: str) -> bool:
    """YouTube Mix / Radio playlists (RD…) are huge and slow to enumerate."""
    pid = _playlist_id(url)
    return bool(pid) and (pid.startswith("RD") or pid.startswith("UL") or pid.startswith("PU"))


def prefer_single_video(url: str) -> bool:
    """If URL is a watch link with a Mix list, treat as single video by default."""
    parsed = urlparse(url)
    path = parsed.path or ""
    if "watch" in path or "youtu.be" in (parsed.netloc or ""):
        return is_mix_url(url)
    return False


def strip_to_video_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if "youtu.be" in (parsed.netloc or ""):
        vid = parsed.path.strip("/").split("/")[0]
        return f"https://www.youtube.com/watch?v={vid}" if vid else url
    qs = parse_qs(parsed.query)
    vid = (qs.get("v") or [""])[0]
    if vid:
        return f"https://www.youtube.com/watch?v={vid}"
    return url


def format_duration(seconds: Optional[int]) -> str:
    if seconds is None:
        return "—"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def format_views(count: Optional[int]) -> str:
    if count is None:
        return "—"
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M views"
    if count >= 1_000:
        return f"{count / 1_000:.1f}K views"
    return f"{count} views"


def format_bytes(n: Optional[float]) -> str:
    if not n:
        return "—"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return "—"


def progress_fraction(d: dict[str, Any]) -> float:
    """Reliable 0..1 progress for progressive + fragmented downloads."""
    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
    done = d.get("downloaded_bytes") or 0
    if total:
        return max(0.0, min(1.0, float(done) / float(total)))

    frag_count = d.get("fragment_count") or 0
    frag_index = d.get("fragment_index") or 0
    if frag_count:
        return max(0.0, min(1.0, float(frag_index) / float(frag_count)))

    # yt-dlp sometimes exposes a parsed percent
    pct = d.get("_percent")
    if isinstance(pct, (int, float)):
        return max(0.0, min(1.0, float(pct) / 100.0))

    return 0.0


def _best_thumbnail(info: dict[str, Any]) -> str:
    thumbs = info.get("thumbnails") or []
    if thumbs:
        # Prefer medium size for speed
        mid = thumbs[len(thumbs) // 2] if len(thumbs) > 2 else thumbs[-1]
        return mid.get("url") or info.get("thumbnail") or ""
    return info.get("thumbnail") or ""


def _collect_video_formats(info: dict[str, Any], ffmpeg: bool) -> list[dict[str, Any]]:
    seen: set[int] = set()
    heights: list[int] = []
    for f in info.get("formats") or []:
        if f.get("vcodec") in (None, "none"):
            continue
        height = f.get("height")
        if not height or height in seen:
            continue
        seen.add(height)
        heights.append(int(height))
    heights.sort(reverse=True)

    result: list[dict[str, Any]] = [
        {"label": "Best available", "value": "best", "kind": "video"},
    ]
    for h in heights:
        result.append({"label": f"{h}p", "value": str(h), "kind": "video"})
    if ffmpeg:
        result.append({"label": "Audio only (MP3)", "value": "audio", "kind": "audio"})
    else:
        result.append({"label": "Audio only (best)", "value": "audio", "kind": "audio"})
    return result


def _playlist_formats(ffmpeg: bool) -> list[dict[str, Any]]:
    base = [
        {"label": "Best available", "value": "best", "kind": "video"},
        {"label": "1080p", "value": "1080", "kind": "video"},
        {"label": "720p", "value": "720", "kind": "video"},
        {"label": "480p", "value": "480", "kind": "video"},
        {"label": "360p", "value": "360", "kind": "video"},
    ]
    if ffmpeg:
        base.append({"label": "Audio only (MP3)", "value": "audio", "kind": "audio"})
    else:
        base.append({"label": "Audio only (best)", "value": "audio", "kind": "audio"})
    return base


def _entry_to_item(entry: dict[str, Any], fallback_channel: str = "") -> Optional[MediaItem]:
    if not entry:
        return None
    vid = entry.get("id") or ""
    webpage = entry.get("url") or entry.get("webpage_url") or ""
    # Flat playlist entries often give watch?v=ID as url, or bare id
    if webpage and not webpage.startswith("http"):
        webpage = f"https://www.youtube.com/watch?v={webpage}"
    if not webpage and vid:
        webpage = f"https://www.youtube.com/watch?v={vid}"
    if not webpage:
        return None
    title = entry.get("title") or "Untitled"
    if title in ("[Deleted video]", "[Private video]"):
        return None
    return MediaItem(
        id=vid or webpage,
        title=title,
        url=webpage,
        duration=entry.get("duration"),
        channel=entry.get("uploader")
        or entry.get("channel")
        or fallback_channel
        or "",
        thumbnail=_best_thumbnail(entry),
        selected=True,
    )


def fetch_info(url: str, load_playlist: bool = False, playlist_limit: int = 100) -> MediaInfo:
    url = url.strip()
    if not url:
        raise ValueError("Please paste a YouTube URL.")

    ffmpeg = has_ffmpeg()
    mix = is_mix_url(url)

    # Mix / radio on a watch URL → single video unless user forces playlist
    if mix and not load_playlist and prefer_single_video(url):
        url = strip_to_video_url(url)
        mix = False

    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": not load_playlist and bool(re.search(r"[?&]v=", url)),
        "extract_flat": "in_playlist",
        "playlistend": playlist_limit if load_playlist else 1,
        "socket_timeout": 20,
    }

    # Explicit playlist URL
    if "/playlist" in url or (load_playlist and _playlist_id(url)):
        opts["noplaylist"] = False
        opts["playlistend"] = playlist_limit

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if info is None:
        raise RuntimeError("Could not read this link.")

    is_playlist = info.get("_type") == "playlist" or (
        info.get("entries") is not None and load_playlist
    )

    if is_playlist and info.get("entries") is not None:
        entries: list[MediaItem] = []
        channel = info.get("uploader") or info.get("channel") or ""
        for entry in info.get("entries") or []:
            item = _entry_to_item(entry, channel)
            if item:
                # Only first item selected by default on huge lists
                item.selected = len(entries) < 20
                entries.append(item)
        thumb = _best_thumbnail(info)
        if not thumb and entries:
            thumb = entries[0].thumbnail
        return MediaInfo(
            kind="playlist",
            title=info.get("title") or "Playlist",
            url=info.get("webpage_url") or url,
            channel=channel,
            thumbnail=thumb,
            webpage_url=info.get("webpage_url") or url,
            is_mix=mix or is_mix_url(url),
            formats=_playlist_formats(ffmpeg),
            entries=entries,
        )

    # Single video — full extract for formats
    opts_full = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 20,
    }
    with yt_dlp.YoutubeDL(opts_full) as ydl:
        info = ydl.extract_info(strip_to_video_url(url), download=False)

    return MediaInfo(
        kind="video",
        title=info.get("title") or "Untitled",
        url=info.get("webpage_url") or url,
        channel=info.get("uploader") or info.get("channel") or "",
        duration=info.get("duration"),
        thumbnail=_best_thumbnail(info),
        description=(info.get("description") or "")[:280],
        view_count=info.get("view_count"),
        webpage_url=info.get("webpage_url") or url,
        is_mix=False,
        formats=_collect_video_formats(info, ffmpeg),
        entries=[
            MediaItem(
                id=info.get("id") or "",
                title=info.get("title") or "Untitled",
                url=info.get("webpage_url") or url,
                duration=info.get("duration"),
                channel=info.get("uploader") or info.get("channel") or "",
                thumbnail=_best_thumbnail(info),
                selected=True,
            )
        ],
    )


class DownloadManager:
    """Runs yt-dlp downloads on a worker thread with cancel support."""

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._cancel = threading.Event()
        self._pause = threading.Event()
        self._pause.set()
        self._ydl: Optional[yt_dlp.YoutubeDL] = None
        self.is_running = False
        self.is_paused = False
        self.last_files: list[str] = []
        self.ok_count = 0
        self.fail_count = 0

    def busy(self) -> bool:
        return self.is_running and self._thread is not None and self._thread.is_alive()

    def cancel(self) -> None:
        self._cancel.set()
        self._pause.set()
        self.is_paused = False

    def pause(self) -> None:
        if not self.busy():
            return
        self.is_paused = True
        self._pause.clear()

    def resume(self) -> None:
        self.is_paused = False
        self._pause.set()

    def _hook(self, progress_cb: Optional[ProgressCallback]):
        def hook(d: dict[str, Any]) -> None:
            if self._cancel.is_set():
                raise yt_dlp.utils.DownloadCancelled("Cancelled by user")
            while not self._pause.is_set():
                if self._cancel.is_set():
                    raise yt_dlp.utils.DownloadCancelled("Cancelled by user")
                self._pause.wait(0.15)
            if progress_cb:
                progress_cb(d)

        return hook

    def _format_selector(self, quality: str) -> tuple[str, list[dict[str, Any]]]:
        """Return (format, postprocessors)."""
        ffmpeg = has_ffmpeg()
        post: list[dict[str, Any]] = []

        if quality == "audio":
            if ffmpeg:
                return "bestaudio/best", [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ]
            return "bestaudio[ext=m4a]/bestaudio/best", post

        # YouTube rarely offers progressive A+V anymore — merge when possible
        if ffmpeg:
            if quality == "best":
                return "bv*+ba/b", post
            return (
                f"bv*[height<={quality}]+ba/"
                f"b[height<={quality}]/"
                f"bv*+ba/b"
            ), post

        # Last resort without ffmpeg (video-only — poor UX, but better than hard fail)
        if quality == "best":
            return "bv*/b", post
        return f"bv*[height<={quality}]/bv*/b", post

    def _build_opts(
        self,
        out_dir: str,
        quality: str,
        progress_cb: Optional[ProgressCallback],
    ) -> dict[str, Any]:
        out_dir = os.path.normpath(os.path.abspath(out_dir))
        os.makedirs(out_dir, exist_ok=True)
        outtmpl = os.path.join(out_dir, "%(title).180B [%(id)s].%(ext)s")
        fmt, post = self._format_selector(quality)
        ffmpeg_path = get_ffmpeg_path()

        opts: dict[str, Any] = {
            "outtmpl": {"default": outtmpl},
            "noplaylist": True,
            "ignoreerrors": False,
            "continuedl": True,
            "retries": 10,
            "fragment_retries": 10,
            "concurrent_fragment_downloads": 5,
            "http_chunk_size": 10485760,
            "progress_hooks": [self._hook(progress_cb)],
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "socket_timeout": 30,
            "format": fmt,
            "windowsfilenames": True,
            "overwrites": False,
        }
        if ffmpeg_path:
            opts["ffmpeg_location"] = ffmpeg_path
        if post:
            opts["postprocessors"] = post
        if ffmpeg_path and quality != "audio":
            opts["merge_output_format"] = "mp4"
        return opts

    def start(
        self,
        urls: list[str],
        out_dir: str,
        quality: str,
        on_progress: Optional[ProgressCallback] = None,
        on_status: Optional[StatusCallback] = None,
        on_item_done: Optional[Callable[[str, bool, str], None]] = None,
        on_finished: Optional[Callable[[int, int, list[str]], None]] = None,
    ) -> None:
        if self.busy():
            raise RuntimeError("A download is already running.")

        self._cancel.clear()
        self._pause.set()
        self.is_paused = False
        self.is_running = True
        self.last_files = []
        self.ok_count = 0
        self.fail_count = 0
        out_dir = os.path.normpath(os.path.abspath(out_dir))

        def worker() -> None:
            try:
                total = len(urls)
                for index, item_url in enumerate(urls, start=1):
                    if self._cancel.is_set():
                        if on_status:
                            on_status("Cancelled.")
                        break
                    if on_status:
                        on_status(f"Downloading {index}/{total}…")

                    opts = self._build_opts(out_dir, quality, on_progress)
                    before = set()
                    try:
                        before = set(os.listdir(out_dir))
                    except OSError:
                        before = set()

                    try:
                        with yt_dlp.YoutubeDL(opts) as ydl:
                            self._ydl = ydl
                            ydl.download([item_url])
                            # Collect prepared filenames if available
                            info = getattr(ydl, "_playlist_urls", None)
                            _ = info
                        self.ok_count += 1
                        # Detect new files
                        try:
                            after = set(os.listdir(out_dir))
                            for name in sorted(after - before):
                                if name.endswith((".part", ".ytdl", ".temp")):
                                    continue
                                self.last_files.append(os.path.join(out_dir, name))
                        except OSError:
                            pass
                        if on_item_done:
                            on_item_done(item_url, True, "Done")
                    except yt_dlp.utils.DownloadCancelled:
                        self.fail_count += 1
                        if on_item_done:
                            on_item_done(item_url, False, "Cancelled")
                        if on_status:
                            on_status("Cancelled.")
                        break
                    except Exception as exc:  # noqa: BLE001
                        self.fail_count += 1
                        msg = str(exc)
                        if "ffmpeg" in msg.lower() and not has_ffmpeg():
                            msg = (
                                "ffmpeg is required for this quality. "
                                "Install ffmpeg or choose Best available "
                                "(app will use a no-merge format)."
                            )
                        if on_item_done:
                            on_item_done(item_url, False, msg)
                        if on_status:
                            on_status(f"Failed ({index}/{total}): {msg}")
                else:
                    if on_status and not self._cancel.is_set():
                        if self.ok_count and not self.fail_count:
                            on_status(
                                f"Done — {self.ok_count} saved to {out_dir}"
                            )
                        elif self.ok_count:
                            on_status(
                                f"Finished with errors — {self.ok_count} ok, "
                                f"{self.fail_count} failed. Folder: {out_dir}"
                            )
                        else:
                            on_status(
                                f"Nothing saved — {self.fail_count} failed. "
                                f"Check quality / ffmpeg. Folder: {out_dir}"
                            )
            finally:
                self._ydl = None
                self.is_running = False
                self.is_paused = False
                if on_finished:
                    on_finished(self.ok_count, self.fail_count, list(self.last_files))

        self._thread = threading.Thread(target=worker, daemon=True)
        self._thread.start()
