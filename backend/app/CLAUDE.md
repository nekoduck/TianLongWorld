# app/
> L2 | 父级: backend/CLAUDE.md

后端应用包。分层自上而下单向依赖：api（路由）→ director（编排）→ session / llm（状态与模型）→ schemas / errors（契约）。main.py 是唯一把它们装配在一起的组合根，其余模块互不 new 对方。

成员清单
__init__.py: 包标识，仅一行导航注释
main.py: 组合根，create_app(settings, director) 装配 LLM + SessionStore + Director + CORS + GameError 统一处理器；模块级 app 供 uvicorn 加载，测试注入替身 director
config.py: 配置唯一入口，pydantic-settings 读取环境变量与 backend/.env（路径锚定于文件而非 cwd），provider 四选一（mock/gemini/openai/anthropic），get_settings() 进程级单例
schemas.py: 协议唯一来源，WorldState（64 字封顶的语义标签，自带 status_bar 投影）/ Options / InteractRequest / DirectorOutput（大模型契约：存活必有选项，含内部字段 present）+ DIRECTOR_SCHEMA（交给大模型做结构化输出）/ InteractResponse / NewSessionResponse
api.py: 路由层，POST /api/session 开局、POST /api/interact 出招、GET /api/health；零业务逻辑，Director 经 app.state 注入
session.py: 会话状态层，Session（state + 最近 N 回合 Turn 记忆 + present 在场名单 + dead/busy 标志，acting() 回合守卫，presence() 在场素材，advance() 推进）与 SessionStore（OrderedDict LRU，get_or_rehydrate 冷启动恢复）
errors.py: 统一错误谱系，GameError 携带 status_code 与玩家可读 message；SessionDead/Busy=409，Director/LLM=502
director/: 导演管线（规则裁决 + 提示词协议 + 解析 + 编排），地图见 director/CLAUDE.md
llm/: 大模型适配（协议 + 厂商客户端 + Mock + 工厂），地图见 llm/CLAUDE.md

设计要点
- ui_status_bar 不进大模型契约：它是 next_state 的纯投影，由 WorldState.status_bar() 确定性渲染
- present 只进大模型契约不进响应：InteractResponse.from_director 显式挑字段构造，内部字段天然不外泄
- 错误响应与 FastAPI 原生 422 同形 {"detail": ...}，前端只需一种解析
- 会话纯内存：进程重启后由客户端快照 current_state 冷启动续玩，死亡标志随之丢失属已知取舍

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
