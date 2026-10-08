"""
[INPUT]: 依赖 application/ports 的 JsonSchema
[OUTPUT]: 对外提供 portable_schema() —— 把 Pydantic 生成的 JSON Schema 规整为三家厂商结构化输出都接受的子集
[POS]: llm 包的契约翻译器：内联 $defs、剥离厂商不支持的约束关键字（长度、数值范围、标题、默认值）、对象一律封闭
       additionalProperties=false；被剥离的约束并未丢失——调用方的 Pydantic 校验在本地照样执行
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from typing import Any

from app.application.ports import JsonSchema

_UNSUPPORTED = frozenset(
    {"title", "default", "minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum",
     "exclusiveMaximum", "multipleOf", "minItems", "maxItems", "pattern", "examples", "uniqueItems"}
)


def portable_schema(schema: JsonSchema) -> JsonSchema:
    defs: dict[str, Any] = schema.get("$defs", {})

    def walk(node: Any, trail: tuple[str, ...]) -> Any:
        if isinstance(node, list):
            return [walk(x, trail) for x in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[-1]
            if name in trail:
                raise ValueError(f"结构化输出不支持递归 schema：{name}")
            return walk(defs[name], (*trail, name))
        out = {k: walk(v, trail) for k, v in node.items() if k not in _UNSUPPORTED and k not in {"$defs", "properties"}}
        if "properties" in node:  # 属性表的键是字段名而非关键字：字段叫 title 也不能被剥掉
            out["properties"] = {name: walk(sub, trail) for name, sub in node["properties"].items()}
        if out.get("type") == "object":
            out["additionalProperties"] = False
        return out

    result: JsonSchema = walk(schema, ())
    return result
