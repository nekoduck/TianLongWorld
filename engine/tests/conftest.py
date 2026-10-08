"""
[INPUT]: 依赖 pytest / pytest-asyncio，依赖 app.application.ports 的 LLMClient，依赖 app.config 的 Settings，依赖 app.container 的 build_container，
         依赖 app.infrastructure.persistence 的内存实现，依赖 tests/world 的 WORLD
[OUTPUT]: 对外提供 ScriptedLLM（按剧本吐出回复并记录每次调用的 system / user / schema；stream 把回复切片吐出）、
          settings（全内存 + mock 的配置）、container（经组合根装配、种下 WORLD 的完整引擎）、spawned_at() 投胎助手、play() 收集回合消息、
          以及 PG_DSN / NEO4J 集成环境变量
[POS]: tests 的公共装置：测试与生产走同一条组合根；真实 PostgreSQL / Neo4j 只在环境变量给出时参与契约测试
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import os
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest

from app.application.bus import Command, SessionOpened, SpawnPlayer, TurnMessage
from app.application.ports import JsonSchema, LLMClient
from app.config import Settings
from app.container import Container, build_container
from tests.world import WORLD

PG_DSN = os.environ.get("TLBB_TEST_POSTGRES_DSN", "")
NEO4J_URI = os.environ.get("TLBB_TEST_NEO4J_URI", "")
NEO4J_USER = os.environ.get("TLBB_TEST_NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("TLBB_TEST_NEO4J_PASSWORD", "")


class ScriptedLLM(LLMClient):
    def __init__(self, *replies: str, chunk: int = 7) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, str, JsonSchema | None]] = []
        self._chunk = chunk

    async def complete(self, system: str, user: str, schema: JsonSchema | None = None) -> str:
        self.calls.append((system, user, schema))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        text = await self.complete(system, user)
        for i in range(0, len(text), self._chunk):
            yield text[i : i + self._chunk]


@pytest.fixture
def settings(tmp_path: Any) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        llm_provider="mock",
        event_store="memory",
        graph_backend="memory",
        qdrant_url=":memory:",
        world_dir=tmp_path / "world",
        source_text_dir=tmp_path / "source",
    )


@pytest.fixture
async def container(settings: Settings) -> AsyncIterator[Container]:
    built = await build_container(settings, blueprint=WORLD)
    yield built
    await built.aclose()


async def play(container: Container, command: Command) -> list[TurnMessage]:
    return [m async for m in container.bus.dispatch(command)]


async def spawned_at(container: Container, location: str, name: str = "阿星") -> str:
    messages = await play(container, SpawnPlayer(name=name, location=location))
    opened = next(m for m in messages if isinstance(m, SessionOpened))
    return opened.player_id


def kinds(messages: Sequence[TurnMessage]) -> list[str]:
    return [type(m).__name__ for m in messages]
