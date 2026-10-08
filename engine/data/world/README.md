# data/world/

原著播种的产物（由 `python -m app.seed` 生成，**入库**；原著全文本身永不入库）：

- `blueprint.json` —— 原著蓝图：抽取与写图之间的中间表示，可人工审阅后再 `apply`
- `seed.cypher` —— 由蓝图确定性生成的写图脚本，可交给 cypher-shell
- `report.txt` —— 组装报告：被丢弃的泛称 / 描述性称呼 / 悬空引用、被封存的武学、抽取失败的文本块
- `cache/<提示词版本>/` —— 逐块抽取记录（按提示词版本 + 文本哈希命名，内容是名称级的结构化记录而非原文，
  与本块原文共享 ≥16 字的描述已清空）：中断后重跑不重复花钱；组装器改了规则，凭缓存零费用重新组装

## 当前切片：前 40 块 = 第一回「青衫磊落险峰行」至第九回「换巢鸾凤」（大理篇）

| 产物 | 口径 |
| --- | --- |
| `blueprint.json` / `seed.cypher` / `report.txt` | 提示词 `tlbb-extract-v4`（gemini-3.1-pro-preview，high）抽取，当前组装器定案：地点 126、人物 74、武学 33、物品 114、关系 125 |
| `cache/tlbb-extract-v4/` | 40/40 块，完整 |
| `cache/tlbb-extract-v5/` | 16/40 块——v5 重抽进行到一半时 Gemini 预付额度耗尽（HTTP 402） |

复现入库的蓝图（需把原著放进 `data/source_text/`，不调用大模型）：

```bash
python -m app.seed assemble --prompt-version tlbb-extract-v4 --max-chunks 40
```

v5 提示词要求处所写作「上级·处所」并填上级地点，组装器据此让上级与处所互通，能把 v4 里剑湖宫、无量山一带的孤岛接进主图。
额度恢复后，下面这条命令只补抽余下的 24 块，蓝图随之切换到 v5 口径：

```bash
python -m app.seed extract --max-chunks 40
```
