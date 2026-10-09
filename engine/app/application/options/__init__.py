"""
[INPUT]: 依赖 options/sources 的 ActionOption / OptionCategory，依赖 options/slate 的 OptionGenerator
[OUTPUT]: 对外提供 ActionOption、OptionCategory、OptionGenerator——拆包之前从 app.application.options 能导入的，拆包之后照样能导入
[POS]: application 的动态选项生成器（显著性菜单）门面，只做转出。包内分工：sources 五类候选源（Thread / Person / Ground / Self / Exit）、
       phrasing 措辞变体、salience 显著性、slate 席位与 MMR、preview 上榜缘由与风险档
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from app.application.options.slate import OptionGenerator
from app.application.options.sources import ActionOption, OptionCategory

__all__ = ["ActionOption", "OptionCategory", "OptionGenerator"]
