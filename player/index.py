from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("xiaogpt.player")

AUDIO_SUFFIXES = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg"}
_INVALID_TITLE = re.compile(r"^(?:tmp|temp|untitled|unknown|track\s*\d*|\d+)$", re.I)


@dataclass(frozen=True)
class Song:
    id: str
    path: Path
    title: str
    artist: str = ""
    album: str = ""
    duration: float = 0.0

    @property
    def display(self) -> str:
        return f"{self.artist}的{self.title}" if self.artist else self.title


def _first_tag(tags, key: str) -> str:
    values = tags.get(key) if tags else None
    return str(values[0]).strip() if values else ""


def _parse_filename(stem: str) -> tuple[str, str]:
    """`歌手 - 歌名` -> (歌手, 歌名)；没有分隔符时整个文件名当歌名。"""
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return artist.strip(), title.strip()
    return "", stem.strip()


def read_song(path: Path, root: Path) -> Song:
    from mutagen import File

    tags = None
    duration = 0.0
    try:
        audio = File(path, easy=True)
        if audio is not None:
            tags = audio.tags
            duration = float(getattr(audio.info, "length", 0) or 0)
    except Exception as e:
        logger.warning("读取标签失败 %s: %s", path, e)

    file_artist, file_title = _parse_filename(path.stem)
    title = _first_tag(tags, "title")
    if not title or _INVALID_TITLE.match(title):
        title = file_title
    artist = _first_tag(tags, "artist") or file_artist
    song_id = hashlib.sha1(str(path.relative_to(root)).encode()).hexdigest()[:12]
    return Song(
        id=song_id,
        path=path,
        title=title,
        artist=artist,
        album=_first_tag(tags, "album"),
        duration=duration,
    )


class MusicLibrary:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self._songs: dict[str, Song] = {}
        self.scanned_at = 0.0

    def scan(self) -> None:
        songs: dict[str, Song] = {}
        if not self.root.is_dir():
            logger.warning("音乐目录不存在：%s", self.root)
        else:
            for path in sorted(self.root.rglob("*")):
                if path.suffix.lower() not in AUDIO_SUFFIXES or not path.is_file():
                    continue
                if any(part.startswith(".") for part in path.relative_to(self.root).parts):
                    continue
                song = read_song(path, self.root)
                songs[song.id] = song
        self._songs = songs
        self.scanned_at = time.monotonic()
        logger.info("曲库扫描完成：%s 首，目录 %s", len(songs), self.root)

    def is_stale(self, seconds: float) -> bool:
        return time.monotonic() - self.scanned_at > seconds

    def get(self, song_id: str) -> Song | None:
        return self._songs.get(song_id)

    @property
    def songs(self) -> list[Song]:
        return list(self._songs.values())
