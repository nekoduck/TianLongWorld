# llm/
> L2 | 父级: backend/app/CLAUDE.md

大模型适配层。对上只暴露 LLMClient 协议（纯文本进、纯文本出，schema 仅作契约提示），结构化解析与最终校验是 director 的职责而非客户端的。三个真实客户端：Gemini 原生（默认推荐，结构化输出）、Anthropic 原生、OpenAI Chat Completions 兼容（OpenAI / DeepSeek / 通义 / Ollama）。

成员清单
__init__.py: 刻意不做 re-export，避免 mock → director.prompts 与 director → llm 形成包级导入环；调用方直接导入 base / factory
base.py: LLMClient 协议，complete(system, user, schema=None) -> str 与 JsonSchema 别名，director 唯一依赖的抽象；不支持结构化输出的实现忽略 schema 即可
_http.py: 私有传输层 post_json()，httpx2 每次新建连接（大模型延迟以秒计，不值得引入连接池生命周期）；超时/连接/4xx5xx/非 JSON 全部收敛为 LLMError，上游报文只进日志
gemini.py: GeminiClient，POST {base}/models/{model}:generateContent，x-goog-api-key 头；systemInstruction + responseJsonSchema 结构化输出 + thinkingConfig.thinkingLevel（默认 low，实测约 5s/回合）；过滤 thought 片段，拒答/空正文收敛为 LLMError
openai_compat.py: OpenAICompatClient，POST {base}/chat/completions，开启 response_format=json_object（忽略 schema：严格 json_schema 模式与 Pydantic schema 不兼容）；不下发 max_tokens，temperature 为 None 时不下发
anthropic.py: AnthropicClient，POST {base}/v1/messages，x-api-key + anthropic-version 头，max_tokens 必填，拼接 content 中全部 text 块；忽略 schema
mock.py: MockLLM 离线导演，像真实模型一样只读提示词协议标签（player_state / opening_seed / player_action / directive）产出合规 JSON，System Prompt 里的参考模块不读；开局把 premise 中点名的绝顶高手写进 local_delta.arrived，回合无人进出；每回合照常做实体提取（next_state.involved_entities：开局 = 地点 + 到场高手，处决 = 地点 + 行刑者 + 杀招，闲逛 = 地点）；玩家"拾/捡"即上报行囊新增「锈蚀铁牌」，"偷听"即上报一条私密情报（player_delta.secrets），"烧/毁"即在 next_state.major_events 上报一条以当前地点为标签、旁观者视角（不点破玩家）的世界大事；回合场景与选项是被动沙盒的样板：只有环境的自然反馈与各忙各的路人；推进时辰、随机天气，可配 latency
factory.py: build_llm(settings) 唯一装配点，mock 无需凭证；gemini / anthropic / openai 缺 LLM_API_KEY 或 LLM_MODEL 时启动即抛错

依赖说明
- mock.py → director/prompts.py 是刻意的反向依赖：Mock 是导演协议的替身，必须读懂同一份协议

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
