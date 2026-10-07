"""
[INPUT]: 依赖 app.director.parser 的 parse_director_output，依赖 app.errors 的 DirectorError，依赖 conftest 的 alive_reply
[OUTPUT]: 大模型输出解析的单测
[POS]: tests 中守护"自由文本 → 强类型协议"闸门的用例集
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import json

import pytest

from app.director.parser import parse_director_output
from app.errors import DirectorError
from conftest import alive_reply


def test_strips_markdown_fence_and_chatter():
    out = parse_director_output(f"好的，裁决如下：\n```json\n{alive_reply()}\n```\n祝游戏愉快")
    assert out.next_state.time == "丑时" and out.options.C == "夺船而走"


def test_alive_without_options_is_rejected():
    reply = json.loads(alive_reply())
    reply["options"] = None
    with pytest.raises(DirectorError):
        parse_director_output(json.dumps(reply))


def test_death_may_omit_options():
    reply = json.loads(alive_reply()) | {"game_over": True, "options": None}
    assert parse_director_output(json.dumps(reply)).game_over


def test_prose_is_rejected():
    with pytest.raises(DirectorError):
        parse_director_output("你被一掌打死了。")
