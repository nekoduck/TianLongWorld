# llm/
> L2 | 父级: backend/app/CLAUDE.md

大模型适配层。对上只暴露 LLMClient 协议（纯文本进、纯文本出，schema 必填）：每个真实客户端都在 API 层强制执行导演契约，从采样层面杜绝格式漂移、Markdown 外壳与寒暄；最终校验仍是 director 解析闸门的职责。厂商 JSON 一律先经 Pydantic 封套收窄，截断/拒答/拦截/形状异常全部收敛为 LLMError。三个真实客户端：Gemini 原生（默认推荐，responseJsonSchema）、Anthropic 原生（强制工具调用）、OpenAI Chat Completions 兼容（json_schema 严格模式，可退回 json_object；OpenAI / DeepSeek / 通义 / Ollama）。

成员清单
__init__.py: 刻意不做 re-export，避免 mock → director.prompts 与 director → llm 形成包级导入环；调用方直接导入 base / factory
base.py: LLMClient 协议，complete(system, user, schema) -> str（schema 必填）与 JsonSchema 别名，director 唯一依赖的抽象；真实客户端必须在 API 层强制 schema，输出仍由 director 解析闸门终审，一切失败收敛为 LLMError
_http.py: 私有传输层，两道关口：post_json() 以 httpx2 每次新建连接（大模型延迟以秒计，不值得引入连接池生命周期），超时/连接/4xx5xx/非 JSON 收敛为 LLMError，返回未经校验的 object；VendorEnvelope 封套基类（frozen + extra=ignore，厂商会加字段）与 parse_envelope() 把形状异常同样收敛为 LLMError；上游报文只进日志
schema.py: strict_json_schema() 纯函数，深拷贝后把 Pydantic schema 整形为 OpenAI 严格模式子集：object 节点 additionalProperties=false 且 required=全部属性，删 default/title/min|maxLength/min|maxItems/pattern/format（长度由 Pydantic 兜底），带 description 的 $ref 展开（环上退回裸引用），开放映射显式拒绝；只有 openai_compat 需要
gemini.py: GeminiClient，POST {base}/models/{model}:generateContent，x-goog-api-key 头；systemInstruction + responseMimeType + responseJsonSchema（直收 Pydantic 原生 schema）+ thinkingConfig.thinkingLevel（默认 low，实测约 5s/回合）；Pydantic 封套解析，过滤 thought 片段，MAX_TOKENS 截断、blockReason 拦截、空正文收敛为 LLMError
openai_compat.py: OpenAICompatClient，POST {base}/chat/completions；strict_schema=True 时 response_format=json_schema（name=director_output, strict=true, schema 经 schema.py 整形），False 时退回 json_object（DeepSeek 等）；Pydantic 封套解析，缺 choices、finish_reason=length 截断、content 为空或 refusal 拒答收敛为 LLMError；不下发 max_tokens，temperature 为 None 时不下发
anthropic.py: AnthropicClient，POST {base}/v1/messages，x-api-key + anthropic-version 头，max_tokens 必填；强制工具调用即结构化输出（tools=[submit_director_output, input_schema=schema] + tool_choice 钉死），取 tool_use.input 以 json.dumps(ensure_ascii=False) 回传；stop_reason=max_tokens 截断、无 tool_use 收敛为 LLMError；不用 beta 头
mock.py: MockLLM 离线导演，像真实模型一样只读提示词协议标签（player_state / local_environment / player_action / opening_seed / directive）产出完整契约 JSON；开局把 premise 中点名的绝顶高手写进 arrived，必死 directive 复用 director/fallback.execution，回合推进一格时辰、随机天气、无人进出；"拾/捡"上报行囊新增「锈蚀铁牌」，"烧/毁"上报一条以当前地点为标签的世界大事；可配 latency
factory.py: build_llm(settings) 唯一装配点，mock 无需凭证；gemini / anthropic / openai 缺 LLM_API_KEY 或 LLM_MODEL 时启动即抛错；LLM_STRICT_SCHEMA → OpenAICompatClient.strict_schema

依赖说明
- mock.py → director/prompts.py、director/fallback.py 是刻意的反向依赖：Mock 是导演协议的替身，必须读懂同一份协议、复用同一份处决叙事

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
