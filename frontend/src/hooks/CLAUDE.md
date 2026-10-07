# hooks/
> L2 | 父级: frontend/CLAUDE.md

前端的全部行为逻辑都在这里；components 因此得以保持为无状态的纯视图（ActionPanel 的输入草稿除外）。

成员清单
useGame.ts: 视图状态机（内部视图 GameView），useReducer 驱动 idle → loading → playing ⇄ loading → dead → loading，阶段只由服务端裁决驱动、不做任何世界规则计算；current 保存上一轮 next_state 整树，下次请求原样作为 current_state 回传；worldId 记住此身所在世界；start() 以 newSession(null) 开辟新世界，rebirth() 以 newSession(worldId) 在同一世界投胎；事件 rebirth（清空此身、保留 worldId）/ request / resolve / reject / perish / vanish；错误码 dead → perish 进入 dead 阶段（保留当前场景、亮出投胎入口，不再陷入 409 循环），not_found → vanish 回 idle 并清空 worldId（"此局已散，请重新入世"），其余错误退回出招前局面并提示；inflight ref 同步拦截同帧双击；turn 计数驱动打字机与面板重置
useTypewriter.ts: 打字机，Array.from 按码点逐字吐出，每字 32ms、标点后 6 倍停顿；以 resetKey 在渲染期重置（不闪现上一幕进度）；skip() 一键显全；prefers-reduced-motion 时直接显全

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
