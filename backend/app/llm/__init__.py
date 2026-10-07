"""
llm 包：大模型调用层。刻意不做 re-export —— mock.py 依赖 director/prompts.py，
包级聚合导入会与 director 形成导入环；调用方请直接导入具体模块（base / factory）。
"""
