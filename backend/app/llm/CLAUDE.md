# llm/
> L2 | 父级: backend/app/CLAUDE.md

大模型适配层。对上只暴露 LLMClient 协议（纯文本进、纯文本出），结构化解析是 director 的职责而非客户端的。两个真实客户端覆盖全部主流厂商：Anthropic 原生协议 + OpenAI Chat Completions 兼容协议（OpenAI / DeepSeek / 通义 / Gemini / Ollama 皆走此路）。

成员清单
__init__.py: 刻意不做 re-export，避免 mock → director.prompts 与 director → llm 形成包级导入环；调用方直接导入 base / factory
base.py: LLMClient 协议，complete(system, user) -> str，director 唯一依赖的抽象
_http.py: 私有传输层 post_json()，httpx2 每次新建连接（大模型延迟以秒计，不值得引入连接池生命周期）；超时/连接/4xx5xx/非 JSON 全部收敛为 LLMError，上游报文只进日志
openai_compat.py: OpenAICompatClient，POST {base}/chat/completions，开启 response_format=json_object；不下发 max_tokens（各家参数名不一），temperature 为 None 时不下发
anthropic.py: AnthropicClient，POST {base}/v1/messages，x-api-key + anthropic-version 头，max_tokens 必填，拼接 content 中全部 text 块
mock.py: MockLLM 离线导演，像真实模型一样读取提示词协议标签（opening/normal/lethal 三种 directive）产出合规 JSON；推进时辰、随机天气，可配 latency 让前端缓冲提示可见
factory.py: build_llm(settings) 唯一装配点，mock 无需凭证；真实厂商缺 LLM_API_KEY 或 LLM_MODEL 时启动即抛错

依赖说明
- mock.py → director/prompts.py 是刻意的反向依赖：Mock 是导演协议的替身，必须读懂同一份协议

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
