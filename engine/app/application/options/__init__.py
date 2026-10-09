"""
[INPUT]: 依赖 options/sources 的 ActionOption / OptionCategory，依赖 options/slate 的 OptionGenerator，依赖 options/menu 的 compose
[OUTPUT]: 对外提供 ActionOption、OptionCategory、OptionGenerator（affordances / catalogue / generate）、compose——拆包之前从 app.application.options 能导入的，拆包之后照样能导入
[POS]: application 的动态选项生成器（显著性菜单 + 意图风味封装）门面，只做转出。包内分工：sources 四类候选源（Thread / Person / Ground / Self）与 ActionOption、
       phrasing 朴素标签的措辞变体、salience 显著性、preview 上榜缘由与风险档、slate 全部可供之招 / 可供性目录 / 退路菜单、menu 说书人挑选的过闸。
       移动不在这里：导航是 application/navigation
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from app.application.options.menu import compose
from app.application.options.slate import OptionGenerator
from app.application.options.sources import ActionOption, OptionCategory

__all__ = ["ActionOption", "OptionCategory", "OptionGenerator", "compose"]
