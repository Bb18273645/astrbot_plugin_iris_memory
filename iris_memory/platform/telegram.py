"""
Iris Chat Memory - Telegram 平台适配器

Telegram 的消息链与 AstrBot 通用形状差异较大：

- 图片在 AstrBot 里被转换为 ``Comp.Image(file=<telegram 文件路径>)``，
  该路径不是可下载 URL，通用适配器只能返回空列表；
- ``raw_message`` 是 python-telegram-bot 的 ``Update`` 对象，没有
  ``message_id`` 字段，依赖 raw 载荷的模块会拿到空 ID；
- 引用消息被转换为 ``Comp.Reply`` 组件，可以直接读取。

本适配器的职责：
1. 从 ``Update`` 里提取本轮（和引用消息里）的图片，并记录 Telegram 的
   ``file_id``；
2. 在异步阶段用 Bot API 把图片下载到插件图片缓存目录，交给统一的
   图片解析链路（``ImageParser`` 只接受 URL 或缓存目录内的本地文件）；
3. 把 ``message_id`` 等内容补进 ``get_raw_message()`` 的返回值。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, Optional

from ..core import get_logger
from ..identity import resolve_user_id
from .base import PlatformAdapter, ReplyInfo

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent
    from ..image.models import ImageInfo

logger = get_logger("platform.telegram")


def _first_non_empty(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


class TelegramAdapter(PlatformAdapter):
    """Telegram 平台适配器。"""

    # ------------------------------------------------------------------
    # 用户与群组
    # ------------------------------------------------------------------
    def get_user_id(self, event: "AstrMessageEvent") -> str:
        try:
            return resolve_user_id(str(event.message_obj.sender.user_id))
        except AttributeError:
            logger.error("无法获取用户ID：event.message_obj.sender.user_id 不存在")
            raise

    def get_user_name(self, event: "AstrMessageEvent") -> str:
        try:
            name = event.message_obj.sender.nickname
        except AttributeError as exc:
            logger.error("无法获取用户名称：event.message_obj.sender.nickname 不存在")
            raise exc
        return str(name) if name else self.get_user_nickname(event)

    def get_user_nickname(self, event: "AstrMessageEvent") -> str:
        user = self._raw_sender(event)
        full_name = _first_non_empty(
            getattr(user, "first_name", ""),
            getattr(user, "last_name", ""),
        )
        return _first_non_empty(
            getattr(user, "username", ""),
            full_name,
            getattr(event.message_obj.sender, "nickname", ""),
        )

    def get_group_id(self, event: "AstrMessageEvent") -> str:
        try:
            group_id = getattr(event.message_obj, "group_id", "")
        except AttributeError:
            logger.error("无法获取群ID：event.message_obj 结构异常")
            raise
        return str(group_id) if group_id else ""

    def get_group_name(self, event: "AstrMessageEvent") -> str:
        return self._structured_group_name(event)

    def get_user_role(self, event: "AstrMessageEvent") -> str:
        return "member" if self.is_group_message(event) else "private"

    def is_group_message(self, event: "AstrMessageEvent") -> bool:
        try:
            return bool(self.get_group_id(event))
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 原始消息
    # ------------------------------------------------------------------
    def get_raw_message(self, event: "AstrMessageEvent") -> dict[str, Any]:
        raw = getattr(event.message_obj, "raw_message", None)
        data: dict[str, Any] = {}
        if isinstance(raw, dict):
            data.update(raw)
        elif raw is not None and hasattr(raw, "__dict__"):
            data.update(raw.__dict__)

        message_id = self._message_id(event)
        if message_id:
            data["message_id"] = message_id
        data["update"] = raw

        sender = self._raw_sender(event)
        if sender is not None:
            data["sender"] = {
                "user_id": getattr(sender, "id", ""),
                "nickname": self.get_user_nickname(event),
                "is_bot": bool(getattr(sender, "is_bot", False)),
            }
        return data

    # ------------------------------------------------------------------
    # 图片
    # ------------------------------------------------------------------
    def get_images(self, event: "AstrMessageEvent") -> List["ImageInfo"]:
        from ..image.models import ImageInfo

        message = self._raw_message(event)
        if message is None:
            return []

        message_id = self._message_id(event)
        images: List[ImageInfo] = []

        for tg_photo in self._photo_candidates(message):
            images.append(
                ImageInfo(
                    url=None,
                    file_path=None,
                    format="jpg",
                    size_kb=0,
                    source="user",
                    message_id=message_id,
                    platform_ref=str(tg_photo.file_id),
                )
            )

        for tg_document in self._image_documents(message):
            document_name = str(getattr(tg_document, "file_name", "") or "")
            images.append(
                ImageInfo(
                    url=None,
                    file_path=None,
                    format=Path(document_name).suffix.lstrip(".").lower(),
                    size_kb=0,
                    source="user",
                    message_id=message_id,
                    platform_ref=str(tg_document.file_id),
                )
            )

        reply = getattr(message, "reply_to_message", None)
        if reply is not None:
            for tg_photo in self._photo_candidates(reply):
                images.append(
                    ImageInfo(
                        url=None,
                        file_path=None,
                        format="jpg",
                        size_kb=0,
                        source="forward",
                        message_id=message_id,
                        platform_ref=str(tg_photo.file_id),
                    )
                )

        if images:
            logger.debug(f"从 Telegram 消息提取到 {len(images)} 张图片")
        return images

    async def prepare_images(
        self, event: "AstrMessageEvent"
    ) -> List["ImageInfo"]:
        """把 Telegram 图片下载到插件图片缓存目录。

        统一图片链路只接受 URL 或缓存目录内的本地文件，Telegram 的
        ``file_id`` 需要先通过 Bot API 下载。
        """
        images = self.get_images(event)
        if not images:
            return []

        bot = self._get_bot(event)
        if bot is None:
            logger.warning("Telegram Bot 实例不可用，跳过图片下载")
            return images

        cache_dir = self._telegram_cache_dir()
        prepared: List["ImageInfo"] = []
        for image_info in images:
            file_reference = image_info.platform_ref
            if not file_reference:
                continue
            try:
                tg_file = await bot.get_file(file_reference)
                suffix = Path(
                    str(getattr(tg_file, "file_path", "") or "")
                ).suffix.lower() or ".jpg"
                destination = cache_dir / f"tg_{uuid.uuid4().hex}{suffix}"
                await tg_file.download_to_drive(custom_path=str(destination))
                if not destination.is_file():
                    logger.debug(f"Telegram 图片下载后不存在：{destination}")
                    continue
                from ..image.security import MAX_IMAGE_BYTES

                size = destination.stat().st_size
                if size <= 0 or size > MAX_IMAGE_BYTES:
                    logger.debug(
                        f"Telegram 图片大小不合法（{size} 字节），跳过：{destination.name}"
                    )
                    try:
                        destination.unlink()
                    except OSError:
                        pass
                    continue
                image_info.file_path = str(destination)
                image_info.size_kb = max(1, size // 1024)
                prepared.append(image_info)
            except Exception as exc:
                logger.debug(f"下载 Telegram 图片失败（已忽略）：{exc}")

        if prepared:
            logger.debug(f"已下载 {len(prepared)} 张 Telegram 图片到本地缓存")
        return prepared

    # ------------------------------------------------------------------
    # 回复
    # ------------------------------------------------------------------
    def get_reply_info(self, event: "AstrMessageEvent") -> ReplyInfo:
        try:
            for component in event.message_obj.message or []:
                if type(component).__name__ != "Reply":
                    continue
                return ReplyInfo(
                    message_id=_first_non_empty(getattr(component, "id", "")),
                    user_id=_first_non_empty(
                        getattr(component, "sender_id", ""),
                        getattr(component, "qq", ""),
                    ),
                    user_name=_first_non_empty(
                        getattr(component, "sender_nickname", ""),
                    ),
                    content=_first_non_empty(
                        getattr(component, "message_str", ""),
                        getattr(component, "text", ""),
                    ),
                )
        except Exception as exc:
            logger.debug(f"解析 Telegram 引用消息失败（已忽略）：{exc}")
        return ReplyInfo()

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    def _message_id(self, event: "AstrMessageEvent") -> str:
        try:
            message_id = getattr(event.message_obj, "message_id", "")
        except AttributeError:
            message_id = ""
        if message_id:
            return str(message_id)
        message = self._raw_message(event)
        return _first_non_empty(getattr(message, "message_id", ""))

    def _raw_message(self, event: "AstrMessageEvent") -> Any:
        raw = getattr(event.message_obj, "raw_message", None)
        if raw is None:
            return None
        return getattr(raw, "message", None) or getattr(raw, "edited_message", None)

    def _raw_sender(self, event: "AstrMessageEvent") -> Any:
        message = self._raw_message(event)
        if message is not None:
            sender = getattr(message, "from_user", None)
            if sender is not None:
                return sender
        return None

    def _get_bot(self, event: "AstrMessageEvent") -> Any:
        raw = getattr(event.message_obj, "raw_message", None)
        get_bot = getattr(raw, "get_bot", None)
        if callable(get_bot):
            try:
                return get_bot()
            except Exception as exc:
                logger.debug(f"通过 Update.get_bot() 获取 Bot 失败：{exc}")
        return getattr(event, "bot", None)

    @staticmethod
    def _photo_candidates(message: Any) -> list[Any]:
        photos = list(getattr(message, "photo", None) or [])
        return photos[-1:] if photos else []

    @staticmethod
    def _image_documents(message: Any) -> list[Any]:
        document = getattr(message, "document", None)
        if document is None:
            return []
        mime_type = str(getattr(document, "mime_type", "") or "")
        if not mime_type.startswith("image/"):
            return []
        return [document]

    @staticmethod
    def _telegram_cache_dir() -> Path:
        from ..config import get_config

        cache_dir = get_config().data_dir / "image_cache" / "telegram"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir
