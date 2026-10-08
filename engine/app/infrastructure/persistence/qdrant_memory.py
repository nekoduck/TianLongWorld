"""
[INPUT]: 依赖 qdrant-client 的 AsyncQdrantClient 与 models（VectorParams / PointStruct / Filter / FieldCondition / MatchValue / Range），
         依赖 domain/ports 的 NarrativeMemory / MemoryRecord，依赖 hashlib 的 blake2b
[OUTPUT]: 对外提供 Embedder 抽象、HashingEmbedder（字符一元 + 二元组哈希向量，零依赖、确定性、中文友好）、
          QdrantNarrativeMemory（ensure_collection / remember / recall / close）
[POS]: persistence 的长线记忆：每条领域事件的确定性白描是一个向量点，主键 = uuid5(玩家:版本)，重放即覆盖（幂等）；
       召回按玩家过滤、只取本回合之前的记忆。url=":memory:" 时走 qdrant-client 的本地模式——同一份代码，零服务可跑；
       Embedder 是可替换的端口：换成真正的语义嵌入模型只需新写一个实现
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import hashlib
import math
from abc import ABC, abstractmethod
from collections.abc import Sequence
from itertools import pairwise
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import AsyncQdrantClient, models

from app.domain.ports import MemoryRecord, NarrativeMemory


class Embedder(ABC):
    dim: int

    @abstractmethod
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class HashingEmbedder(Embedder):
    """字符 n-gram 的符号哈希投影（hashing trick）。不懂语义，但懂"同一批人名地名武功名"——对事件白描的召回足够。"""

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    def _features(self, text: str) -> list[tuple[str, float]]:
        chars = [c for c in text if not c.isspace()]
        return [(c, 0.5) for c in chars] + [(a + b, 1.0) for a, b in pairwise(chars)]

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vec = [0.0] * self.dim
            for feature, weight in self._features(text):
                digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
                bucket = int.from_bytes(digest[:4], "little") % self.dim
                vec[bucket] += weight if digest[4] & 1 else -weight
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            vectors.append([v / norm for v in vec])
        return vectors


class QdrantNarrativeMemory(NarrativeMemory):
    def __init__(
        self, client: AsyncQdrantClient, *, collection: str, embedder: Embedder, index_payload: bool = True
    ) -> None:
        self._client = client
        self._collection = collection
        self._embedder = embedder
        self._index_payload = index_payload  # 本地模式不支持载荷索引，建了也只会告警

    @classmethod
    async def connect(
        cls, url: str, *, api_key: str = "", collection: str, embedder: Embedder | None = None
    ) -> "QdrantNarrativeMemory":
        local = url == ":memory:"
        client = AsyncQdrantClient(location=":memory:") if local else AsyncQdrantClient(url=url, api_key=api_key or None)
        memory = cls(client, collection=collection, embedder=embedder or HashingEmbedder(), index_payload=not local)
        await memory.ensure_collection()
        return memory

    async def ensure_collection(self) -> None:
        if await self._client.collection_exists(self._collection):
            return
        await self._client.create_collection(
            self._collection,
            vectors_config=models.VectorParams(size=self._embedder.dim, distance=models.Distance.COSINE),
        )
        if self._index_payload:
            await self._client.create_payload_index(self._collection, "player_id", models.PayloadSchemaType.KEYWORD)
            await self._client.create_payload_index(self._collection, "version", models.PayloadSchemaType.INTEGER)

    async def close(self) -> None:
        await self._client.close()

    async def remember(self, records: Sequence[MemoryRecord]) -> None:
        if not records:
            return
        vectors = self._embedder.embed([r.text for r in records])
        await self._client.upsert(
            self._collection,
            points=[
                models.PointStruct(
                    id=str(uuid5(NAMESPACE_URL, f"tlbb:{r.player_id}:{r.version}")),
                    vector=vector,
                    payload={"player_id": r.player_id, "version": r.version, "text": r.text},
                )
                for r, vector in zip(records, vectors, strict=True)
            ],
        )

    async def recall(self, player_id: str, query: str, limit: int, before_version: int) -> list[MemoryRecord]:
        if not query.strip() or before_version <= 1:
            return []
        result = await self._client.query_points(
            self._collection,
            query=self._embedder.embed([query])[0],
            query_filter=models.Filter(must=[
                models.FieldCondition(key="player_id", match=models.MatchValue(value=player_id)),
                models.FieldCondition(key="version", range=models.Range(lt=before_version)),
            ]),
            limit=limit,
            with_payload=True,
        )
        return [
            MemoryRecord(player_id=p.payload["player_id"], version=int(p.payload["version"]), text=p.payload["text"])
            for p in result.points
            if p.payload
        ]
