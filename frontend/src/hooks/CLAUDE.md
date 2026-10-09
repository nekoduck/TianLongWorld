# hooks/
> L2 | 父级: frontend/CLAUDE.md

前端的全部行为逻辑都在这里；两个状态机按 VITE_ENGINE 二选一，都交付 view.ts 的 GameFacade；components 因此得以保持为无状态的纯视图（ActionPanel 的输入草稿除外）。

成员清单
useGame.ts: backend 游戏状态机（内部视图 GameView），useReducer 驱动 idle → loading → playing ⇄ loading → dead；current 保存上一轮 next_state 整树，下次请求原样作为 current_state 回传；事件 rebirth / request / resolve / reject；inflight ref 同步拦截同帧双击；推演失败退回出招前局面；turn 计数驱动打字机与面板重置；A/B/C 映射为通用 Choice（观 / 探 / 险，value 即选项原文）
useEngineGame.ts: engine 游戏状态机，与 useGame 同形（view.GameFacade）另带 resume；帧驱动 idle → loading → streaming → playing ⇄ … → dead：turn_resolved 开新一幕（turn+1）并以 facts（驳回则以理由）立题记，narration_delta 累加进 scene，turn_completed 落定选项（3~4 招 → Choice，hint 为 risk · why，tone 先看风险档再看方向）、状态栏（PlayerStatus 渲染为「 | 」六段）与生死；error 帧不断线，在途回合退回或停在半幕；「悄悄续局」（resync）：断线重连、OPTION_EXPIRED、连接被 Fast Refresh 的 cleanup 关掉（effect 换新连接并交接 player ref）都先锁进 loading（旧菜单作废、不得出招，提示语随缓冲提示显示），quiet resume 的终帧只换菜单、状态栏与生死——此景、题记、turn（打字机进度与输入草稿）原样保留，零大模型；页面刷新后的续前缘才复述此景；名号以 session 帧为准，重新投胎沿用（缺省无名氏）；死亡、PLAYER_DEAD、UNKNOWN_PLAYER 都 forget 并收起续前缘，UNKNOWN_PLAYER 回入世页只给玩家看的话、不露内部 id；busy ref 同步拦截同帧双发
useTypewriter.ts: 打字机，Array.from 按码点逐字吐出，每字 32ms、标点后 6 倍停顿；以 resetKey 在渲染期重置（不闪现上一幕进度），文本增长时接着吐字——计时 effect 只随进度走、字形从 ref 读，流式叙事每来一片不会清掉在途的那一拍；skip() 一键显全；prefers-reduced-motion 时直接显全

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
