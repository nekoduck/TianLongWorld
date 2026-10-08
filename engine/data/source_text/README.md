# data/source_text/

把《天龙八部》原著全文（.txt，UTF-8 或 GBK/GB18030 均可）放在这里，然后运行：

```bash
python -m app.seed export --out /tmp/jobs --max-chunks 40   # 导出开篇四十块：也是世界的时间切片
# Claude 子代理照 EXTRACTION_SYSTEM.txt 逐块作答，再逐块 ingest
python -m app.seed ingest --index 0 --file chunk-000.json
python -m app.seed assemble --max-chunks 40                 # 零费用组装蓝图
```

抽取由 Claude 子代理完成，不调用付费大模型；`extract --use-llm` 才会调用 engine/.env 配置的大模型（会产生费用）。

原著受著作权保护：本目录下的 .txt 已被 .gitignore 排除，永不入库。
