"""DeepZoom tile server: OpenSlide DeepZoomGenerator (254 px tiles, overlap 1, JPEG q=85)
with an LRU cache of open slides."""

from __future__ import annotations

import io
import threading
from collections import OrderedDict
from pathlib import Path

import openslide
from openslide.deepzoom import DeepZoomGenerator

TILE_SIZE = 254
OVERLAP = 1
JPEG_QUALITY = 85
CACHE_SIZE = 16


class _SlideCache:
    def __init__(self, size: int):
        self.size = size
        self._items: OrderedDict[str, tuple[openslide.OpenSlide, DeepZoomGenerator]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str, path: Path) -> DeepZoomGenerator:
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key][1]
        slide = openslide.OpenSlide(str(path))
        dz = DeepZoomGenerator(slide, tile_size=TILE_SIZE, overlap=OVERLAP, limit_bounds=False)
        with self._lock:
            self._items[key] = (slide, dz)
            self._items.move_to_end(key)
            while len(self._items) > self.size:
                _, (old, _) = self._items.popitem(last=False)
                old.close()
        return dz

    def evict(self, key: str) -> None:
        with self._lock:
            item = self._items.pop(key, None)
        if item:
            item[0].close()


cache = _SlideCache(CACHE_SIZE)


def dzi_xml(key: str, path: Path) -> str:
    return cache.get(key, path).get_dzi("jpeg")


def tile_jpeg(key: str, path: Path, level: int, col: int, row: int) -> bytes:
    dz = cache.get(key, path)
    if not 0 <= level < dz.level_count:
        raise ValueError("level out of range")
    cols, rows = dz.level_tiles[level]
    if not (0 <= col < cols and 0 <= row < rows):
        raise ValueError("tile out of range")
    buf = io.BytesIO()
    dz.get_tile(level, (col, row)).convert("RGB").save(buf, "JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()
