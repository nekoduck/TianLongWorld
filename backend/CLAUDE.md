# backend/
> L2 | 父级: /CLAUDE.md

FastAPI 服务的工程根：依赖声明、配置模板、类型闸门、测试装置。业务代码全部位于 app/ 包内，cwd 须为 backend/（`app.main:app` 与 pytest 的 pythonpath 均以此为根）；运行期事件库落在 data/（gitignore）。

成员清单
requirements.txt: 运行依赖，fastapi + uvicorn[standard] + pydantic v2 + pydantic-settings + httpx2（大模型 HTTP 调用与 TestClient 共用）；事件库用标准库 sqlite3，无额外依赖
requirements-dev.txt: 开发依赖，在运行依赖之上叠加 pytest 与 mypy
pytest.ini: 测试配置，pythonpath=. 使 `import app` 生效，testpaths=tests
mypy.ini: 类型闸门配置，strict + pydantic.mypy 插件（init_typed / init_forbid_extra），files 与 mypy_path 以 $MYPY_CONFIG_FILE_DIR 锚定，覆盖 app 与 tests
.env.example: 配置模板，列出 LLM_PROVIDER / LLM_API_KEY / LLM_MODEL / LLM_THINKING_LEVEL / LLM_STRICT_SCHEMA / DATABASE_PATH / HISTORY_TURNS / MEMORY_LIMIT 等全部可调项及 gemini / anthropic / openai 兼容示例；真实密钥写入同目录 .env（已 gitignore）
app/: 应用包（契约、事件、事件库、纯函数内核、编排、模型适配、组合根），地图见 app/CLAUDE.md
tests/conftest.py: 公共装置，ScriptedLLM 剧本替身（可抛异常、记录 system/prompt/schema）+ alive()/dead()/snapshot()/reply() 由契约模型构造报文 + begin_life() 直写开局事件 + make_director() + db_path/settings/store/director/client 夹具（事件库落在 tmp_path，settings 经 model_construct 隔离本机 .env）
tests/test_engine.py: 纯函数内核用例，裁决（开局只认种子且种子在场者恒登记、四本账记账与绝学防线、时辰只进不退、死状、必死作废增减与世界大事、复述的世界大事驳回、局部环境按身份认人与同图/换图判据）与投影（折叠等价、滑动窗口截断、流完整性）
tests/test_store.py: 事件库用例，读写往返、触发器禁止 UPDATE/DELETE、乐观并发与原子回滚、同一世界跨命追加、重开文件持久化
tests/test_memory.py: JIT 召回用例，地点/别名/身份/动作点名四路命中、点名单向、单字不命中、无关排除、保序与 limit
tests/test_prompts.py: 提示词协议用例，PARCER 两行逐字与六段顺序、示例可通过契约校验、示例不把小偷小摸记成世界大事、XML 输入只含注入事实（无世界整树、只含相关记忆、窗口逐行 JSON 不可伪造）、防注入、必死指令属性、带错重采样回灌
tests/test_pipeline.py: 编排器用例，正常回合原子追加、幻觉原地停顿不落库、带错重采样、LLMError 502 与恢复、必死在抗命/失败下的确定性处决、死者/未知/并发守卫、JIT（含动作点名）与窗口与换地图注入、复述的世界大事不重复落库、开局兜底与同世界投胎
tests/test_api.py: HTTP 集成用例，开局与出招、跨 app 实例持久化、投胎保留世界大事、404/409/502 的 {detail, code}、客户端篡改无效、422 校验
tests/test_lethal.py: 致死预判单测，"敌意 ∧ 点名 ∧ 在场（只读实体账）∧ 无绝学（只读 martial_arts）"与无害复合词剔除
tests/test_parser.py: 解析闸门单测，围栏/寒暄/尾随文本、hints 字段路径与上限、存活必有选项、多余字段与重复选项拒收
tests/test_llm_clients.py: 厂商客户端单测，替换 post_json 断言报文形状（Gemini responseJsonSchema、OpenAI json_schema strict 与退回、Anthropic output_config.format）、截断/拒答/一切非正常结束/形状异常收敛为 LLMError、strict_json_schema 整形，以及缺凭证启动即失败
tests/test_mock.py: Mock 导演用例，读取真实 prompt 产出合规契约：开局在场者、拾物、毁地、必死处决、时辰推进
tests/test_typing.py: 类型闸门，以 mypy.api 按 mypy.ini 检查 app 与 tests，零错误才放行

运行
  python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
  .venv/bin/uvicorn app.main:app --reload --port 8000
  .venv/bin/pytest -q

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
