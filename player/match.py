from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Iterable

from player.index import Song

MATCH_THRESHOLD = 0.6
_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)


def normalize(text: str) -> str:
    return _NON_WORD.sub("", text.lower())


def _similarity(query: str, field: str) -> float:
    field = normalize(field)
    if not query or not field:
        return 0.0
    if query == field:
        return 1.0
    if len(query) >= 2 and query in field:
        return 0.9
    if len(field) >= 2 and field in query:
        return 0.85
    return SequenceMatcher(None, query, field).ratio()


def score(song: Song, query: str) -> float:
    q = normalize(query)
    title_score = max(
        _similarity(q, song.title),
        _similarity(q, song.path.stem),
        _similarity(q, song.album) * 0.9,
    )
    artist_score = _similarity(q, song.artist) * 0.95
    combined = 0.0
    if "的" in query:
        artist_part, title_part = query.split("的", 1)
        if artist_part and title_part:
            combined = (
                _similarity(normalize(artist_part), song.artist)
                + max(
                    _similarity(normalize(title_part), song.title),
                    _similarity(normalize(title_part), song.path.stem),
                )
            ) / 2
    return max(title_score, artist_score, combined)


def search(songs: Iterable[Song], query: str, threshold: float = MATCH_THRESHOLD) -> list[Song]:
    scored = [(score(song, query), song) for song in songs]
    return [song for s, song in sorted(scored, key=lambda x: x[0], reverse=True) if s >= threshold]
