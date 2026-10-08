# data/world/

原著播种的产物（由 `python -m app.seed extract` 生成，已被 .gitignore 排除）：

- `blueprint.json` —— 原著蓝图：抽取与写图之间的中间表示，可人工审阅后再 `apply`
- `seed.cypher` —— 由蓝图确定性生成的写图脚本，可交给 cypher-shell
- `report.txt` —— 组装报告：被丢弃的悬空引用、被封存的武学、抽取失败的文本块
- `cache/` —— 逐块抽取缓存（按提示词版本 + 文本哈希），中断后重跑不重复花钱
