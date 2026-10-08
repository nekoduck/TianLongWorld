# data/source_text/

把《天龙八部》原著全文（.txt，UTF-8 或 GBK/GB18030 均可）放在这里，然后运行：

```bash
python -m app.seed extract --max-chunks 40   # 先试跑开篇四十块：也是世界的时间切片
python -m app.seed extract --apply --reset   # 全书抽取并写入 Neo4j
```

原著受著作权保护：本目录下的 .txt 已被 .gitignore 排除，永不入库。
