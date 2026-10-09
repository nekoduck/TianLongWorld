# llm/
> L2 | 父级: engine/app/infrastructure/CLAUDE.md

大模型厂商适配。对上只实现 application/ports.py 的 LLMClient（complete + stream），结构化解析与最终校验是调用方 Pydantic 的职责。
自 backend/app/llm 演化而来（同一套 httpx2 直连范式），新增 SSE 流式与跨厂商 schema 规整；引擎取代 backend 时旧适配随之退场。

成员清单
_http.py: 私有传输层，post_json 一次性 JSON POST、stream_sse 逐条解析 SSE 的 data 负载（吞掉 [DONE] 与注释行，坏行只记日志）；超时 / 连接 / 4xx5xx / 非 JSON 全部收敛为 LLMError（408 / 429 / 5xx 与网络故障可重试，其余 4xx 不可重试），上游报文只进日志；429 的报文写明按天计的配额（per_day / insufficient_quota）时改判不可重试——几秒后重试只会再撞一次墙
schema.py: portable_schema——内联 $defs（拒绝递归）、剥离厂商不支持的约束关键字（长度、数值范围、标题、默认值等）、对象一律 additionalProperties=false；属性表按字段名原样保留（字段叫 title 也不会被误删）
anthropic.py: AnthropicClient，POST /v1/messages；output_config.format 原生结构化输出、可选 output_config.effort；temperature 未配置一律不下发（Claude 新模型拒收采样参数）；流式解析 content_block_delta.text_delta；stop_reason 为 refusal / max_tokens 收敛为 LLMError
gemini.py: GeminiClient，generateContent + responseJsonSchema 结构化输出，streamGenerateContent?alt=sse 流式，可选 thinkingConfig.thinkingLevel（Gemini 3 系；部分型号不收 minimal，由配置负责选对）；x-goog-api-key 头而非 URL 传密钥；过滤 thought 片段，拒答类 finishReason 与 blockReason 收敛为 LLMError，一次性补全遇 MAX_TOKENS 截断即报错（半截 JSON 不必等到解析才发现）
openai_compat.py: OpenAICompatClient，Chat Completions；只在需要结构化输出时开 json_object（叙事是散文），不用严格 json_schema 模式（与可选字段契约不兼容）；流式读 choices[0].delta.content，content_filter 收敛为 LLMError
budget.py: 计费护栏——CallBudget 每进程各职责共用的调用次数保险丝（LLM_CALL_LIMIT，0 不设限）、BudgetedLLM 装饰器（每次 complete / stream 先过保险丝，熔断抛不可重试的 LLMError、请求不发出；厂商内部退避重试不另计），不懂价格只管兜底
factory.py: build_llm(settings, role, budget) 唯一装配点，给了保险丝就套上 BudgetedLLM，五种职责（意图 / 叙事 / 地下城主（撞见与狭路相逢的判官也是它）/ 抽取 / 议程）各取（模型, 思考档位）：Gemini 映射为 thinkingLevel、Anthropic 映射为 effort（minimal 按 low）、openai 兼容端忽略；mock 返回 None 交由组合根换上离线实现；真实厂商缺 LLM_API_KEY 或该职责的模型启动即抛错
__init__.py: 包标识

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
