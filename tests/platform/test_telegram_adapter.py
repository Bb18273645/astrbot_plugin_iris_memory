"""Telegram 适配器测试（图片下载、消息 ID、引用与工厂注册）。"""

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest
from astrbot.api.message_components import Reply

from astrbot_plugin_iris_memory.iris_memory.platform import factory
from astrbot_plugin_iris_memory.iris_memory.platform import get_adapter
from astrbot_plugin_iris_memory.iris_memory.platform.telegram import TelegramAdapter

from .fakes import FakeAstrBotMessage, FakeEvent, FakeGroup, FakeMessageMember

_CONFIG_PATCH = "astrbot_plugin_iris_memory.iris_memory.config.get_config"


@dataclass
class FakePhoto:
    file_id: str = "PHOTO_FILE_ID"
    file_unique_id: str = "PHOTO_UNIQUE"
    width: int = 90
    height: int = 90


@dataclass
class FakeDocument:
    file_id: str = "DOC_FILE_ID"
    file_name: str = "cat.png"
    mime_type: str = "image/png"


@dataclass
class FakeTgUser:
    id: int = 8495766039
    username: str = "bbzv0186"
    first_name: str = "柠夏"
    is_bot: bool = False


class FakeTgMessage:
    def __init__(
        self,
        message_id: int = 555,
        photo=None,
        document=None,
        reply_to_message=None,
        from_user=None,
    ):
        self.message_id = message_id
        self.photo = photo or []
        self.document = document
        self.reply_to_message = reply_to_message
        self.from_user = from_user or FakeTgUser()


class FakeTgFile:
    file_path = "photos/file_1.jpg"

    def __init__(self, data: bytes = b"\xff\xd8\xff\xe0fakejpeg"):
        self._data = data

    async def download_to_drive(self, custom_path: str):
        Path(custom_path).write_bytes(self._data)


class FakeBot:
    def __init__(self):
        self.requested: list[str] = []

    async def get_file(self, file_id: str):
        self.requested.append(file_id)
        return FakeTgFile()


class FakeUpdate:
    def __init__(self, message, bot=None):
        self.message = message
        self.edited_message = None
        self._bot = bot or FakeBot()

    def get_bot(self):
        return self._bot


def _make_event(message=None, platform="telegram", message_id=555):
    message = message or FakeTgMessage()
    update = FakeUpdate(message)
    message_obj = FakeAstrBotMessage(
        sender=FakeMessageMember(user_id="8495766039", nickname=None),
        raw_message=update,
        message=[],
        message_id=message_id,
    )
    return FakeEvent(message_obj=message_obj, platform=platform, bot=update.get_bot())


def _config(tmp_path: Path):
    config = type("Config", (), {})()
    config.data_dir = tmp_path
    config.get = lambda key, default=None: default
    return config


def test_factory_returns_telegram_adapter():
    factory._ADAPTER_INSTANCES.pop("telegram", None)
    adapter = get_adapter(_make_event())
    assert isinstance(adapter, TelegramAdapter)


def test_images_use_file_id_and_message_id():
    message = FakeTgMessage(photo=[FakePhoto(), FakePhoto(file_id="LARGEST")])
    adapter = TelegramAdapter()

    images = adapter.get_images(_make_event(message))

    assert len(images) == 1
    assert images[0].platform_ref == "LARGEST"
    assert images[0].message_id == "555"
    assert images[0].has_url is False


def test_image_documents_are_included():
    message = FakeTgMessage(document=FakeDocument())
    adapter = TelegramAdapter()

    images = adapter.get_images(_make_event(message))

    assert len(images) == 1
    assert images[0].platform_ref == "DOC_FILE_ID"
    assert images[0].format == "png"


@pytest.mark.asyncio
async def test_prepare_images_downloads_into_cache_dir(tmp_path):
    message = FakeTgMessage(photo=[FakePhoto()])
    event = _make_event(message)
    adapter = TelegramAdapter()

    with patch(_CONFIG_PATCH, return_value=_config(tmp_path)):
        images = await adapter.prepare_images(event)

    assert len(images) == 1
    file_path = Path(images[0].file_path)
    assert file_path.is_file()
    assert tmp_path / "image_cache" in file_path.parents
    assert images[0].size_kb >= 1
    assert event.message_obj.raw_message.get_bot().requested == ["PHOTO_FILE_ID"]


@pytest.mark.asyncio
async def test_prepare_images_without_bot_returns_originals():
    message = FakeTgMessage(photo=[FakePhoto()])
    event = _make_event(message)
    event.message_obj.raw_message.get_bot = lambda: None
    event.bot = None
    adapter = TelegramAdapter()

    images = await adapter.prepare_images(event)

    assert len(images) == 1
    assert images[0].file_path is None


def test_raw_message_contains_message_id_and_sender():
    adapter = TelegramAdapter()

    raw = adapter.get_raw_message(_make_event())

    assert raw["message_id"] == "555"
    assert raw["sender"]["user_id"] == 8495766039
    assert raw["sender"]["is_bot"] is False


def test_reply_info_from_component():
    event = _make_event()
    event.message_obj.message = [
        Reply(
            id=4242,
            sender_id="777",
            sender_nickname="对方",
            message_str="被引用的内容",
        )
    ]
    adapter = TelegramAdapter()

    reply = adapter.get_reply_info(event)

    assert reply.has_reply is True
    assert reply.message_id == "4242"
    assert reply.user_id == "777"
    assert reply.user_name == "对方"
    assert reply.content == "被引用的内容"


def test_group_and_role():
    message = FakeTgMessage()
    event = _make_event(message)
    event.message_obj.group = FakeGroup(group_id="-100123")
    adapter = TelegramAdapter()

    assert adapter.get_group_id(event) == "-100123"
    assert adapter.is_group_message(event) is True
    assert adapter.get_user_role(event) == "member"
