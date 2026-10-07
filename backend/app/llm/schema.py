"""
[INPUT]: 依赖 copy 的 deepcopy，依赖 llm/base.py 的 JsonSchema
[OUTPUT]: 对外提供 strict_json_schema() —— 把 Pydantic 生成的 JSON Schema 整形为严格模式受限解码兼容的子集（OpenAI / Anthropic 通用）；
          开放映射（additionalProperties 为 true 的裸 dict，或为子 schema 的 dict[str, X]）一律抛 ValueError，绝不静默封闭
[POS]: llm 包的契约整形器（纯函数、无 I/O、不改入参），被 openai_compat.py（response_format）与 anthropic.py（output_config.format）
       在下发前调用——两家的受限解码都只认严格子集：不支持长度约束、对象必须闭合；Gemini 的 responseJsonSchema 直收 Pydantic 原生 schema。
       整形的复杂度因此被隔离在这一个文件里
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import copy

from app.llm.base import JsonSchema

# 严格模式不支持或无益的关键字：长度/条数/格式类约束由服务端 Pydantic 校验兜底，title/default 只是元信息，徒耗 token
_DROPPED = frozenset({"default", "title", "minLength", "maxLength", "minItems", "maxItems", "pattern", "format"})
# 值为「名称 → 子 schema」映射的关键字：只递归映射的值；名称本身是数据，一个叫 title 的字段绝不能被当作关键字删掉
_SCHEMA_MAPS = frozenset({"properties", "$defs"})
# 值为子 schema 列表的组合关键字
_SCHEMA_LISTS = frozenset({"anyOf", "allOf", "oneOf"})


def strict_json_schema(schema: JsonSchema) -> JsonSchema:
    """
    整形规则：
    - 每个 object 节点 additionalProperties=false，required 等于全部 properties 键（严格模式要求字段全部必填）；
    - additionalProperties 只接受缺省或 false：true（裸 dict）与子 schema（dict[str, X]，含与 true 等价的空子 schema {}）
      都是开放映射，一律抛 ValueError——判据是 `is not False` 而非真值，空 dict 是假值却同样开放；
    - 递归删除 _DROPPED 中的关键字，保留 description / type / properties / items / required / anyOf / $ref / $defs / enum / const；
    - 带兄弟关键字的 $ref（Pydantic 把字段 description 挂在 $ref 旁）就地展开为被引用的定义，兄弟关键字优先 ——
      严格模式拒收这种形状，而直接丢弃 description 又会让模型失去字段语义。
    入参常是模块级常量 DIRECTOR_SCHEMA：先深拷贝再整形，任何别名都不会回写到原 schema。
    """
    root = copy.deepcopy(schema)
    return _strict(root, root, frozenset())


def _strict(node: JsonSchema, root: JsonSchema, expanding: frozenset[str]) -> JsonSchema:
    ref = node.get("$ref")
    if isinstance(ref, str) and len(node) > 1:
        siblings = {key: value for key, value in node.items() if key != "$ref"}
        if ref in expanding:
            # 自引用环（递归模型的字段带 description）：再展开就无穷递归，退回裸引用，代价仅是丢一条 description
            node = {"$ref": ref}
        else:
            node = {**_resolve(root, ref), **siblings}
            expanding = expanding | {ref}

    out: JsonSchema = {}
    for key, value in node.items():
        if key in _DROPPED:
            continue
        if key in _SCHEMA_MAPS:
            out[key] = {name: _strict(sub, root, expanding) for name, sub in value.items()}
        elif key == "items":
            out[key] = _strict(value, root, expanding)
        elif key in _SCHEMA_LISTS:
            out[key] = [_strict(sub, root, expanding) for sub in value]
        else:
            out[key] = value

    if out.get("type") == "object" or "properties" in out:
        extra = out.get("additionalProperties", False)
        if extra is not False:
            # 开放映射在严格模式下无从表达：true（裸 dict）与子 schema（dict[str, X]）同理，
            # 强行改成 false 会让受限解码只允许输出 {}，字段形同作废——宁可在下发前显式失败
            raise ValueError(
                f"严格模式无法表达开放映射（additionalProperties={extra!r}）：封闭后模型只能输出 {{}}，"
                "请把 dict 字段改为显式字段的模型"
            )
        out["additionalProperties"] = False
        out["required"] = list(out.get("properties", {}))
    return out


def _resolve(root: JsonSchema, ref: str) -> JsonSchema:
    """解析文档内 JSON Pointer 引用（如 #/$defs/TagDelta）；外部引用与悬空引用都是契约错误，直接失败。"""
    if not ref.startswith("#/"):
        raise ValueError(f"严格模式整形只支持文档内引用：{ref}")
    target: object = root
    for token in ref[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        target = target.get(token) if isinstance(target, dict) else None
    if not isinstance(target, dict):
        raise ValueError(f"无法解析 schema 引用：{ref}")
    return target
