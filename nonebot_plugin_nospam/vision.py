from __future__ import annotations

import asyncio
import hashlib
import math
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Final

import httpx
from nonebot.log import logger
from nonebot_plugin_alconna.uniseg import Image as UniImage
from nonebot_plugin_alconna.uniseg import image_fetch
from PIL import Image, ImageOps, UnidentifiedImageError

from .models import ImageFingerprint

if TYPE_CHECKING:
    from nonebot.adapters import Bot, Event

    from .types import NormalizedMap

DOWNLOAD_TIMEOUT: Final[float] = 4.0
DOWNLOAD_MAX_BYTES: Final[int] = 8 * 1024 * 1024
CACHE_TTL_SECONDS: Final[float] = 300.0
BLOCK_GRID_SIZE: Final[int] = 8
BLOCK_IMAGE_SIZE: Final[int] = 64
HASH_SIZE: Final[int] = 16
ASPECT_RATIO_TOLERANCE_MILLI: Final[int] = 10
AVERAGE_HASH_MAX_DISTANCE: Final[int] = 6
DIFFERENCE_HASH_MAX_DISTANCE: Final[int] = 8


@dataclass(slots=True)
class _CachedFingerprint:
    expires_at: float
    fingerprint: ImageFingerprint


@dataclass(frozen=True, slots=True)
class _SignatureThreshold:
    mean_threshold: float
    max_threshold: int
    changed_block_threshold: int
    changed_block_limit: int


_fingerprint_cache: dict[str, _CachedFingerprint] = {}
_BLOCK_SIGNATURE_THRESHOLD: Final[_SignatureThreshold] = _SignatureThreshold(
    mean_threshold=4.0,
    max_threshold=20,
    changed_block_threshold=10,
    changed_block_limit=2,
)
_EDGE_SIGNATURE_THRESHOLD: Final[_SignatureThreshold] = _SignatureThreshold(
    mean_threshold=6.0,
    max_threshold=24,
    changed_block_threshold=14,
    changed_block_limit=2,
)


async def build_image_visual_payload(
    segment: UniImage,
    *,
    bot: Bot,
    event: Event,
) -> tuple[NormalizedMap, NormalizedMap, ImageFingerprint | None]:
    """构建图片消息段的视觉归一化结果"""
    fingerprint = await get_image_fingerprint(segment, bot=bot, event=event)
    if fingerprint is None:
        return (
            {"type": segment.type.casefold(), "data": {}},
            {"type": segment.type.casefold(), "data": {}},
            None,
        )

    exact_payload: NormalizedMap = {
        "type": segment.type.casefold(),
        "data": {
            "kind": "visual",
            "sha256": fingerprint.sha256,
            "width": fingerprint.width,
            "height": fingerprint.height,
        },
    }
    fuzzy_payload: NormalizedMap = {
        "type": segment.type.casefold(),
        "data": {
            "kind": "visual",
            "aspect_ratio_milli": fingerprint.aspect_ratio_milli,
            "average_hash": fingerprint.average_hash,
            "difference_hash": fingerprint.difference_hash,
            "block_signature": list(fingerprint.block_signature),
            "edge_signature": list(fingerprint.edge_signature),
        },
    }
    return exact_payload, fuzzy_payload, fingerprint


async def get_image_fingerprint(
    segment: UniImage,
    *,
    bot: Bot,
    event: Event,
) -> ImageFingerprint | None:
    """从图片消息段提取视觉指纹"""
    cache_key = _build_image_cache_key(segment, bot=bot)
    if cache_key is None:
        return None

    cached = _get_cached_fingerprint(cache_key)
    if cached is not None:
        return cached

    image_bytes = await _resolve_image_bytes(segment, bot=bot, event=event)
    if image_bytes is None:
        return None

    fingerprint = await asyncio.to_thread(_fingerprint_from_bytes, image_bytes)
    if fingerprint is None:
        return None

    _fingerprint_cache[cache_key] = _CachedFingerprint(
        expires_at=monotonic() + CACHE_TTL_SECONDS,
        fingerprint=fingerprint,
    )
    return fingerprint


def images_are_same(
    left: ImageFingerprint,
    right: ImageFingerprint,
) -> bool:
    """保守判断两张图片是否可视为同一张图"""
    if left.sha256 == right.sha256:
        return True

    if (
        abs(left.aspect_ratio_milli - right.aspect_ratio_milli)
        > ASPECT_RATIO_TOLERANCE_MILLI
    ):
        return False

    if (
        _hamming_distance(left.average_hash, right.average_hash)
        > AVERAGE_HASH_MAX_DISTANCE
    ):
        return False

    if (
        _hamming_distance(left.difference_hash, right.difference_hash)
        > DIFFERENCE_HASH_MAX_DISTANCE
    ):
        return False

    if not _signature_is_close(
        left.block_signature,
        right.block_signature,
        threshold=_BLOCK_SIGNATURE_THRESHOLD,
    ):
        return False

    return _signature_is_close(
        left.edge_signature,
        right.edge_signature,
        threshold=_EDGE_SIGNATURE_THRESHOLD,
    )


def _get_cached_fingerprint(cache_key: str) -> ImageFingerprint | None:
    cached = _fingerprint_cache.get(cache_key)
    if cached is None:
        return None

    if cached.expires_at <= monotonic():
        _fingerprint_cache.pop(cache_key, None)
        return None

    return cached.fingerprint


def _build_image_cache_key(segment: UniImage, *, bot: Bot) -> str | None:
    if segment.raw:
        return f"raw:{hashlib.sha256(segment.raw_bytes).hexdigest()}"
    if segment.path:
        return f"path:{Path(segment.path).expanduser().resolve()}"
    if segment.url:
        return f"url:{segment.url}"
    if segment.id:
        return f"id:{bot.adapter.get_name()}:{bot.self_id}:{segment.id}"
    return None


async def _resolve_image_bytes(
    segment: UniImage,
    *,
    bot: Bot,
    event: Event,
) -> bytes | None:
    image_bytes: bytes | None = None
    if segment.raw:
        image_bytes = segment.raw_bytes
    elif segment.path:
        try:
            image_bytes = await asyncio.to_thread(
                _read_image_path_bytes,
                segment.path,
            )
        except OSError as exception:
            logger.opt(exception=exception).debug(
                "防刷屏 读取图片路径失败，跳过视觉检测",
            )
    elif segment.url:
        image_bytes = await _download_image_bytes(segment.url)
    elif segment.id:
        try:
            image_bytes = await image_fetch(event, bot, {}, segment)
        except Exception as exception:  # noqa: BLE001
            logger.opt(exception=exception).debug(
                "防刷屏 拉取适配器图片资源失败，跳过视觉检测",
            )
    return image_bytes


def _read_image_path_bytes(path: str | Path) -> bytes:
    return Path(path).expanduser().read_bytes()


async def _download_image_bytes(source_url: str) -> bytes | None:
    try:
        async with (
            httpx.AsyncClient(
                follow_redirects=True,
                timeout=DOWNLOAD_TIMEOUT,
            ) as client,
        ):
            client.headers.pop("User-Agent", None)
            async with client.stream("GET", source_url) as response:
                response.raise_for_status()
                payload = bytearray()
                async for chunk in response.aiter_bytes():
                    payload.extend(chunk)
                    if len(payload) > DOWNLOAD_MAX_BYTES:
                        logger.debug(
                            "防刷屏 图片 {} 下载体积超过限制，跳过视觉检测",
                            source_url,
                        )
                        return None
    except (OSError, TimeoutError, httpx.HTTPError) as exception:
        logger.opt(exception=exception).debug(
            "防刷屏 下载图片 {} 失败，跳过视觉检测",
            source_url,
        )
        return None

    return bytes(payload)


def _fingerprint_from_bytes(image_bytes: bytes) -> ImageFingerprint | None:
    try:
        with Image.open(BytesIO(image_bytes)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
    except (OSError, ValueError, UnidentifiedImageError) as exception:
        logger.opt(exception=exception).debug("防刷屏 解析图片失败，跳过视觉检测")
        return None

    width, height = image.size
    if width <= 0 or height <= 0:
        return None

    grayscale_hash = image.convert("L")
    average_hash = _average_hash(grayscale_hash)
    difference_hash = _difference_hash(grayscale_hash)
    block_signature = _block_signature(grayscale_hash)
    edge_signature = _edge_signature(grayscale_hash)

    return ImageFingerprint(
        sha256=hashlib.sha256(image_bytes).hexdigest(),
        width=width,
        height=height,
        aspect_ratio_milli=round(width / height * 1000),
        average_hash=average_hash,
        difference_hash=difference_hash,
        block_signature=block_signature,
        edge_signature=edge_signature,
    )


def _average_hash(image: Image.Image) -> str:
    resized = image.resize((HASH_SIZE, HASH_SIZE), Image.Resampling.LANCZOS)
    pixels = list(resized.tobytes())
    average_value = sum(pixels) / len(pixels)
    bits = "".join("1" if pixel >= average_value else "0" for pixel in pixels)
    return _bits_to_hex(bits)


def _difference_hash(image: Image.Image) -> str:
    resized = image.resize((HASH_SIZE + 1, HASH_SIZE), Image.Resampling.LANCZOS)
    pixels = list(resized.tobytes())
    rows = [
        pixels[index : index + HASH_SIZE + 1]
        for index in range(0, len(pixels), HASH_SIZE + 1)
    ]
    bits = "".join(
        "1" if row[column] >= row[column + 1] else "0"
        for row in rows
        for column in range(HASH_SIZE)
    )
    return _bits_to_hex(bits)


def _block_signature(image: Image.Image) -> tuple[int, ...]:
    resized = image.resize(
        (BLOCK_IMAGE_SIZE, BLOCK_IMAGE_SIZE),
        Image.Resampling.LANCZOS,
    )
    pixels = list(resized.tobytes())
    block_size = BLOCK_IMAGE_SIZE // BLOCK_GRID_SIZE
    signature: list[int] = []
    for block_y in range(BLOCK_GRID_SIZE):
        for block_x in range(BLOCK_GRID_SIZE):
            values: list[int] = []
            start_y = block_y * block_size
            start_x = block_x * block_size
            for y in range(start_y, start_y + block_size):
                offset = y * BLOCK_IMAGE_SIZE
                values.extend(pixels[offset + start_x : offset + start_x + block_size])
            signature.append(round(sum(values) / len(values)))
    return tuple(signature)


def _edge_signature(image: Image.Image) -> tuple[int, ...]:
    resized = image.resize(
        (BLOCK_IMAGE_SIZE, BLOCK_IMAGE_SIZE),
        Image.Resampling.LANCZOS,
    )
    pixels = list(resized.tobytes())
    block_size = BLOCK_IMAGE_SIZE // BLOCK_GRID_SIZE
    signature: list[int] = []
    for block_y in range(BLOCK_GRID_SIZE):
        for block_x in range(BLOCK_GRID_SIZE):
            values: list[int] = []
            start_y = block_y * block_size
            start_x = block_x * block_size
            for y in range(start_y, start_y + block_size):
                for x in range(start_x, start_x + block_size):
                    pixel = pixels[y * BLOCK_IMAGE_SIZE + x]
                    right = pixels[
                        y * BLOCK_IMAGE_SIZE + min(x + 1, BLOCK_IMAGE_SIZE - 1)
                    ]
                    down = pixels[
                        min(y + 1, BLOCK_IMAGE_SIZE - 1) * BLOCK_IMAGE_SIZE + x
                    ]
                    gradient = abs(pixel - right) + abs(pixel - down)
                    values.append(min(255, gradient))
            signature.append(round(sum(values) / len(values)))
    return tuple(signature)


def _signature_is_close(
    left: tuple[int, ...],
    right: tuple[int, ...],
    threshold: _SignatureThreshold,
) -> bool:
    if len(left) != len(right):
        return False

    diffs = [
        abs(left_value - right_value)
        for left_value, right_value in zip(left, right, strict=True)
    ]
    if not diffs:
        return False

    mean_diff = sum(diffs) / len(diffs)
    max_diff = max(diffs)
    changed_blocks = sum(
        diff >= threshold.changed_block_threshold for diff in diffs
    )
    return (
        mean_diff <= threshold.mean_threshold
        and max_diff <= threshold.max_threshold
        and changed_blocks <= threshold.changed_block_limit
    )


def _hamming_distance(left_hex: str, right_hex: str) -> int:
    left_bits = int(left_hex, 16)
    right_bits = int(right_hex, 16)
    return (left_bits ^ right_bits).bit_count()


def _bits_to_hex(bits: str) -> str:
    width = math.ceil(len(bits) / 4)
    return f"{int(bits, 2):0{width}x}"
