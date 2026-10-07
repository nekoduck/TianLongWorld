"""
[INPUT]: 依赖标准库 json / typing，依赖 pytest 的 raises / parametrize，依赖 app.director.parser 的 parse_director_output，
         依赖 app.errors 的 DirectorError，依赖 conftest 的 alive / dead / reply 报文工厂
[OUTPUT]: 解析闸门单测：跳过围栏与寒暄、只解码首个完整对象（尾随文本里的花括号无害）、无 JSON / 非法 JSON / 校验失败均抛带 hints 的
          DirectorError（hints 指明字段路径且至多 6 条）、存活必有选项而死亡可无、多余字段（含快照里夹带的标签账）与重复选项拒收
[POS]: tests 中守护"大模型自由文本 → 强类型 DirectorOutput"最后一道关的纯函数用例集；报文由契约模型序列化后再做定点篡改
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import json
from typing import Any

import pytest

from app.director.parser import parse_director_output
from app.errors import DirectorError
from conftest import alive, dead, reply

OUT = alive()


def _tampered(fields: dict[str, Any]) -> str:
    """以合规报文为底，覆写指定顶层字段后再序列化。"""
    return json.dumps(OUT.model_dump(mode="json") | fields, ensure_ascii=False)


def _rejected(raw: str) -> tuple[str, ...]:
    with pytest.raises(DirectorError) as caught:
        parse_director_output(raw)
    return caught.value.hints


# ============================================================
#  宽容解析 —— 从首个 "{" 起只取第一个完整对象
# ============================================================
@pytest.mark.parametrize(
    "raw",
    [
        f"好的，裁决如下：\n```json\n{reply(OUT)}\n```\n祝游戏愉快",
        f'{reply(OUT)}\n附注：也可改判 {{"game_over": true}}，或者 {{未完',
    ],
    ids=["fence_and_chatter", "trailing_braces"],
)
def test_extracts_first_complete_json_object(raw: str) -> None:
    assert parse_director_output(raw) == OUT


# ============================================================
#  失败即带 hints —— 供重采样回灌
# ============================================================
def test_text_without_json_is_rejected_with_hints() -> None:
    assert _rejected("你被一掌打死了。")


def test_malformed_json_hints_mention_json() -> None:
    hints = _rejected(reply(OUT)[:-20])  # 输出被截断
    assert any("JSON" in hint for hint in hints)


@pytest.mark.parametrize(
    ("fields", "path"),
    [
        ({"next_state": OUT.next_state.model_dump() | {"time": ""}}, "next_state.time"),
        ({"options": {"A": "静观其变", "B": "低声询问"}}, "options.C"),
    ],
    ids=["snapshot_field", "option_slot"],
)
def test_validation_hints_name_the_field_path(fields: dict[str, Any], path: str) -> None:
    assert any(hint.startswith(f"{path}：") for hint in _rejected(_tampered(fields)))


def test_hints_are_capped_at_six() -> None:
    assert len(_rejected("{}")) == 6  # 七个必填字段全缺


# ============================================================
#  契约 —— 存活必有选项、禁止多余字段、三选项互异
# ============================================================
def test_alive_without_options_is_rejected() -> None:
    _rejected(_tampered({"options": None}))


def test_death_may_carry_no_options() -> None:
    out = dead()
    assert parse_director_output(reply(out)) == out


@pytest.mark.parametrize(
    "fields",
    [
        {"ui_status_bar": "【位置：无锡松鹤楼】"},
        {"next_state": OUT.next_state.model_dump() | {"martial_arts": ["北冥神功"]}},
    ],
    ids=["top_level", "ledger_smuggled_into_snapshot"],
)
def test_extra_fields_are_rejected(fields: dict[str, Any]) -> None:
    _rejected(_tampered(fields))


def test_duplicate_options_are_rejected() -> None:
    _rejected(_tampered({"options": {"A": "静观其变", "B": "静观其变", "C": "夺门而走"}}))
