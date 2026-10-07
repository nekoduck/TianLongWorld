# backend/
> L2 | 父级: /CLAUDE.md

FastAPI 服务的工程根：依赖声明、配置模板、测试装置。业务代码全部位于 app/ 包内，cwd 须为 backend/（`app.main:app` 与 pytest 的 pythonpath 均以此为根）。

成员清单
requirements.txt: 运行依赖，fastapi + uvicorn[standard] + pydantic v2 + pydantic-settings + httpx2（大模型 HTTP 调用与 TestClient 共用）
requirements-dev.txt: 开发依赖，在运行依赖之上叠加 pytest
pytest.ini: 测试配置，pythonpath=. 使 `import app` 生效，testpaths=tests
.env.example: 配置模板，列出 LLM_PROVIDER / LLM_API_KEY / LLM_MODEL / LLM_THINKING_LEVEL / HISTORY_TURNS / GRAPH_LIMIT / SEMANTIC_TOP_K 等全部可调项及 gemini / anthropic / openai 兼容示例；真实密钥写入同目录 .env（已 gitignore）
app/: 应用包（协议、会话、路由、组合根），地图见 app/CLAUDE.md
tests/conftest.py: 公共装置，ScriptedLLM 剧本替身（systems 记 System Prompt、prompts 记 User Message）+ alive_reply(scene, arrived, departed, player_delta, major_events, involved, **快照字段) 报文工厂 + game 状态树 / store / director / client 夹具，经 create_app(director=...) 注入
tests/test_api.py: 集成用例，覆盖开局、推演、必死封印、叙述隐名时靠局部环境 present_npcs 判在场（种子点名的高手同样在场）、永久死亡 409、整树冷启动（含带标签的世界台账）、伪造武学被服务端否决、重试与 502、422 校验（含旧版扁平状态与旧版字符串台账被拒）
tests/test_ledger.py: 记账用例，reconcile 五本账（四本标签账 + secrets；遗漏≠失去、先减后加、模糊匹配多义不动、去重）+ chronicle 世界台账（只追加、复述去重、不设上限不合并、大模型无法借 next_state 抹掉旧事）+ 管线集成（五本账多回合不提仍在、快照走私被忽略、解毒/拜帮/烧楼一回合三账、状态栏格式、开局与整树冷启动、开局的情报与大事同样经记忆仓储落账、Mock 写出带地点标签的大事）
tests/test_memory_service.py: 记忆仓储契约用例，抽象接口与缺方法的实现不可实例化、工厂按会话划定命名空间 + 关系图（规格原例两端命中先于一跳、别名 / 修饰称呼 / 门派群体 / 地点包含、台账大事是排在原著之前的动态边、单字不命中、封顶取最近、一条大事伪造不出第二行、秘密不入关系网）+ 语义检索（点名召回、常识按关键词、别名、近事优先与 top_k、及格线挡噪声、秘密不入语料、同一大事两路同形）+ 落账（公开只追加去重、私密包含去重满员请走最早、仍是秘密的事不入台账而揭穿后照记、形状不对拒收）
tests/test_memory.py: RAG 上下文管道用例，System Prompt 以静态法则为前缀挂两个参考模块、历史不再进 User Message + 关系图种子（上一回合 involved_entities 经可见性闸门 + 在场 NPC + 所在地 + 公开身份、"丐帮弟子"端到端、开局实体种下第一回合、Mock 同样提取）+ 语义检索（抵达回合即见目的地历史、与关系网去重且名额不落空）+ 局部环境（同图只认增减、切换地图强制清空、按身份与修饰称呼认人、满员高手优先、开局种子高手不惧地点漂移）+ 载荷恒定与注入防护（台账 0→500 条两份 Prompt 一字不变、检索封顶取最近、窗口封顶且成对、段落与模块都无法闭合或伪造、大事经模块往返后复述去重）+ 契约边界（单条大事上限、新增大事与实体提取超额规整而非 502、配置钉死范围且经组合根交到导演）
tests/test_fog.py: 情报隔离与被动沙盒用例，System Prompt 逐字含用户规定的铁律与克制原则且位于记账规则之前、参考模块标明不是 NPC 的共同记忆、<secrets> 与 <player_state> 结构分离且逐行 JSON 不可伪造不可闭合、回合指令带落笔前自查、secrets 从不作 RAG 检索键（当众打听照常召回）、只活在秘密里的实体种不进关系图、内心念头不触发语义检索、私密情报进 secrets 不进世界台账不上状态栏（双写时私密优先、同名公开事实照记）、情报账按包含去重且满员请走最早的（经管线端到端验证）、大模型情报增减截断不 502、当众揭穿时移出且同回合的公开后果照记、同回合先退场旧说法再收新知、next_state 走私被忽略、契约边界、冷启动保留 secrets 且旧客户端兼容、Mock"偷听"走通私密账、放火只记旁观者视角
tests/test_lethal.py: 致死预判单测，守护"敌意 ∧ 点名 ∧ 在场 ∧ 无绝学（读 martial_arts）"四要素与无害复合词剔除；情报隔离：内心念头不是冒犯、念头之后的当面冒犯照杀、点名只算表面行为、"暗自"出手仍看得见
tests/test_parser.py: 解析闸门单测，围栏/寒暄剥离、存活必有选项、死亡可无选项、散文拒收
tests/test_llm_clients.py: 厂商客户端单测，替换 post_json 断言报文形状（Gemini 的 schema/思考档位/思考片段过滤/拒答），以及缺凭证启动即失败

运行
  python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
  .venv/bin/uvicorn app.main:app --reload --port 8000
  .venv/bin/pytest -q

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
