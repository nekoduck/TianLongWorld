"""
[INPUT]: 依赖 domain/ports 的 WorldReader / WorldProjector / WorldSeeder，依赖 domain/aggregates 的 PlayerState / evolve，
         依赖 domain/models 的本体与 WorldBlueprint，依赖 domain/lore 的 Fact，依赖 domain/snapshot 的视图，依赖 app.errors 的 ProjectionError
[OUTPUT]: 对外提供 InMemoryWorldGraph —— 图谱三端口的进程内实现（快照含 P1 的 era / lead / persona / facts / hostile_ahead / 物性，
          以及语义物理引擎的 clocks / emerged）
[POS]: persistence 的零依赖图谱：正典是一份 WorldBlueprint 的索引，每个平行世界的覆盖层就是一个 PlayerState——
       投影直接复用领域的 evolve 折叠（投影与聚合根同构，无第二套状态机：熟练度、气血、悟性都原样投进快照）；
       下落不明的物品没有持有者，因而不出现在任何快照里；后来才到场（arrives_with）的人与物 P1 不进任何场景；
       用掉的东西（PlayerState.consumed）不进 items / 行囊；出口的 hostile_ahead 读覆盖层里对玩家敌视且未被制住的去处在场者；
       人设只给外显部分（PersonaView），见闻在知情人之一在场、或玩家已知（PlayerState.known_facts）且其主体或 unlock 目标
       在场（此地、在场者、可见之物）时进快照，known 标明已知，主体与知情人去重保序（与 Neo4j 的 MERGE 同口径）；
       labels 把 fact:<slug> 映射为见闻正文；
       叙事时钟与微观事实同样由 evolve 折叠（PlayerState.clocks / emerged，后者至多 EMERGED_MAX 条、重提即刷新）：时钟挂在此地、
       在场者、可见之物或玩家自己身上才进快照，微观事实的主体与 {此地, 在场者, 可见之物} 有交集才进快照；抹去重放随覆盖层一并重建；
       与 Neo4jWorldGraph 同守一份契约（tests/test_world_graph.py 双实现共跑，快照逐字段相等）
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections.abc import Iterable, Sequence

from app.domain.aggregates import PlayerState, evolve
from app.domain.events import EventEnvelope
from app.domain.lore import Fact
from app.domain.models import Attitude, Character, CharacterStatus, WorldBlueprint
from app.domain.ports import WorldProjector, WorldReader, WorldSeeder
from app.domain.snapshot import (
    BondView,
    CharacterView,
    EmergedView,
    ExitView,
    FactView,
    ItemView,
    LocalSnapshot,
    LocationView,
    PersonaView,
    SkillView,
)
from app.errors import ProjectionError


class InMemoryWorldGraph(WorldReader, WorldProjector, WorldSeeder):
    def __init__(self) -> None:
        self._bp = WorldBlueprint()
        self._overlays: dict[str, tuple[PlayerState, int]] = {}
        self._reindex()

    def _reindex(self) -> None:
        bp = self._bp
        self._locations = {x.id: x for x in bp.locations}
        self._characters = {x.id: x for x in bp.characters}
        self._arts = {x.id: x for x in bp.martial_arts}
        self._items = {x.id: x for x in bp.items}
        self._facts = {x.id: x for x in bp.facts}
        self._personas = {
            p.character_id: PersonaView(likes=p.likes, dislikes=p.dislikes, worry=p.worry) for p in bp.personas
        }
        self._bonds: dict[str, list[BondView]] = {}  # 关系无向：两端各记一条，source 一方为上首（lead）
        for rel in bp.relations:
            self._bonds.setdefault(rel.source_id, []).append(
                BondView(other_id=rel.target_id, kind=rel.kind, era=rel.era, lead=True))
            self._bonds.setdefault(rel.target_id, []).append(
                BondView(other_id=rel.source_id, kind=rel.kind, era=rel.era, lead=False))

    # ---------------- 播种 ----------------
    async def seed(self, blueprint: WorldBlueprint, *, reset: bool = False) -> None:
        if reset:
            self._overlays.clear()
        self._bp = blueprint
        self._reindex()

    async def is_seeded(self) -> bool:
        return bool(self._locations)

    # ---------------- 投影 ----------------
    async def project(self, player_id: str, envelopes: Sequence[EventEnvelope]) -> int:
        state, version = self._overlays.get(player_id, (None, 0))
        for envelope in envelopes:
            if envelope.version <= version:
                continue  # 幂等：检查点之前的事件已在覆盖层里
            if envelope.version != version + 1:
                raise ProjectionError(f"{player_id} 的投影缺口：检查点 {version}，收到第 {envelope.version} 版")
            state, version = evolve(state, envelope.event), envelope.version
        if state is not None:
            self._overlays[player_id] = (state, version)
        return version

    async def checkpoint(self, player_id: str) -> int:
        return self._overlays.get(player_id, (None, 0))[1]

    async def forget(self, player_id: str) -> None:
        self._overlays.pop(player_id, None)

    # ---------------- 查询 ----------------
    async def spawn_points(self) -> tuple[str, ...]:
        return tuple(sorted(loc.id for loc in self._locations.values() if loc.exits))

    async def labels(self, ids: Iterable[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for any_id in ids:
            if any_id in self._overlays:
                out[any_id] = self._overlays[any_id][0].name
            elif entity := (self._locations.get(any_id) or self._characters.get(any_id)
                            or self._arts.get(any_id) or self._items.get(any_id)):
                out[any_id] = entity.name
            elif fact := self._facts.get(any_id):
                out[any_id] = fact.text  # 见闻的名字就是它的正文
        return out

    def _holder(self, state: PlayerState, item_id: str) -> str | None:
        return state.item_holders.get(item_id) or self._items[item_id].canon_holder

    def _present(self, location_id: str) -> list[Character]:
        """在场口径：正典所在于此、健在、且不是后来才到场的人（arrives_with 者 P1 不进任何场景）。"""
        return [
            c for c in self._characters.values()
            if c.location_id == location_id and c.status is CharacterStatus.ALIVE and c.arrives_with is None
        ]

    def _hostile_ahead(self, st: PlayerState, location_id: str) -> bool:
        """去处此刻站着对玩家敌视、且没被制住的在场者。"""
        return any(
            st.attitude_of(c.id) is Attitude.HOSTILE and c.id not in st.subdued for c in self._present(location_id)
        )

    @staticmethod
    def _fact_here(fact: Fact, st: PlayerState, here: set[str], scene: set[str]) -> bool:
        """见闻进场的两条路：知情人之一在场；或玩家在此世界已知它，且其主体或 unlock 目标在场（线人不在也用得上）。"""
        if here & set(fact.knower_ids):
            return True
        anchors = {*fact.subject_ids, *([fact.unlock.target_id] if fact.unlock else [])}
        return fact.id in st.known_facts and bool(anchors & scene)

    async def local_snapshot(self, player_id: str) -> LocalSnapshot:
        if player_id not in self._overlays:
            raise ProjectionError(f"图谱中没有 {player_id} 的覆盖层")
        st, version = self._overlays[player_id]
        loc = self._locations[st.location_id]

        present = self._present(loc.id)
        holders = {loc.id, player_id, *(c.id for c in present)}
        consumed = st.consumed
        items = [
            ItemView(
                id=i.id, name=i.name, aliases=i.aliases, kind=i.kind, description=i.description,
                holder_id=holder, owner_id=i.owner_id, portable=i.portable, hazard=i.hazard, use=i.use,
            )
            for i in self._items.values()
            if i.arrives_with is None and i.id not in consumed
            and (holder := self._holder(st, i.id)) is not None and holder in holders
        ]
        inventory = {i.id for i in items if i.holder_id == player_id}
        wanted = set(st.skills) | {s for c in present for s in c.skills}
        wanted |= {a.id for a in self._arts.values() if inventory & set(a.acquisition.items)}
        here = {c.id for c in present}
        scene = {loc.id, *here, *(i.id for i in items)}  # 此地、在场者、看得见的物（地上 / 人手 / 行囊）

        snapshot = LocalSnapshot(
            player_id=player_id,
            player_name=st.name,
            alive=st.alive,
            version=version,
            location=LocationView(id=loc.id, name=loc.name, region=loc.region, description=loc.description),
            exits=[
                ExitView(label=k, to_id=v, to_name=self._locations[v].name, hostile_ahead=self._hostile_ahead(st, v))
                for k, v in loc.exits.items()
            ],
            characters=[
                CharacterView(
                    id=c.id, name=c.true_name, titles=c.titles, aliases=c.aliases, faction=c.faction, tier=c.tier,
                    disposition=c.disposition, description=c.description,
                    subdued=c.id in st.subdued, attitude=st.attitude_of(c.id),
                    skill_ids=c.skills, bonds=self._bonds.get(c.id, []), persona=self._personas.get(c.id),
                )
                for c in present
            ],
            items=items,
            skills=[
                SkillView(
                    id=a.id, name=a.name, aliases=a.aliases, tier=a.tier, kind=a.kind, faction=a.faction,
                    description=a.description, acquisition=a.acquisition, practice=a.practice,
                )
                for a in (self._arts[s] for s in wanted)
            ],
            player_practice=dict(st.practice),
            player_aptitude=st.aptitude,
            player_hp=st.hp,
            facts=[
                FactView(
                    id=f.id, text=f.text, unlock=f.unlock, known=f.id in st.known_facts,
                    # 去重保序：Neo4j 的 MERGE 把重复的 ABOUT / KNOWS_FACT 边合成一条，两边才逐字段相等
                    subject_ids=tuple(dict.fromkeys(f.subject_ids)), knower_ids=tuple(dict.fromkeys(f.knower_ids)),
                )
                for f in self._facts.values() if self._fact_here(f, st, here, scene)
            ],
            # 语义物理引擎的此世之物：时钟挂在眼前之物或玩家自己身上才召回，微观事实点了此地、在场者或可见之物之名才召回
            clocks=[c for c in st.clocks if c.anchor_id in scene or c.anchor_id == player_id],
            emerged=[
                EmergedView(id=e.id, text=e.text, subject_ids=e.subject_ids)
                for e in st.emerged if scene & set(e.subject_ids)
            ],
        )
        return snapshot.model_copy(update={"labels": await self.labels(snapshot.referenced_ids())})
