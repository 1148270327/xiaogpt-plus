from __future__ import annotations

import json
import logging
from pathlib import Path

from player.index import MusicLibrary, Song

logger = logging.getLogger("xiaogpt.player")


class Favorites:
    """收藏列表，按歌曲相对音乐目录的路径保存，重新扫描曲库后仍然对得上。"""

    def __init__(self, path: str | Path, library: MusicLibrary) -> None:
        self.path = Path(path)
        self.library = library
        self._paths: list[str] = self._load()

    def _load(self) -> list[str]:
        if not self.path.is_file():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning("读取收藏失败 %s：%s", self.path, e)
            return []
        return [str(p) for p in data if isinstance(p, str)]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self._paths, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def _key(self, song: Song) -> str:
        return song.path.relative_to(self.library.root).as_posix()

    def __contains__(self, song: Song) -> bool:
        return self._key(song) in self._paths

    def add(self, song: Song) -> bool:
        """加入收藏，原本就在收藏里返回 False。"""
        key = self._key(song)
        if key in self._paths:
            return False
        self._paths.append(key)
        self._save()
        return True

    def remove(self, song: Song) -> bool:
        """移出收藏，原本不在收藏里返回 False。"""
        key = self._key(song)
        if key not in self._paths:
            return False
        self._paths.remove(key)
        self._save()
        return True

    @property
    def songs(self) -> list[Song]:
        """收藏里还在曲库中的歌，按收藏顺序。文件被删掉的歌跳过但不从收藏里抹掉。"""
        by_path = {self._key(s): s for s in self.library.songs}
        return [by_path[p] for p in self._paths if p in by_path]
