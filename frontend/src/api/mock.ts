/**
 * [INPUT]: 依赖 types.ts 的协议类型
 * [OUTPUT]: 对外提供 mockApi（GameApi 的静态实现）
 * [POS]: api 的离线替身：npm run dev:mock 时取代 httpApi，脱离后端跑通打字机、交互与死亡锁死
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
 */
import type { InteractRequest, InteractResponse, NewSessionResponse, Options, WorldState } from '../types'

const LATENCY_MS = 1200
const DEATH_WORDS = /杀|刺|偷袭|挑衅|吐口水|骂/

interface Frame {
  state: WorldState
  scene: string
  options: Options
}

const FRAMES: Frame[] = [
  {
    state: { location: '太湖畔', time: '子时', weather: '微雨', physical_state: '略感风寒', inventory: [] },
    scene:
      '冷雨打在太湖的芦苇上，你蜷在一条破船的篷下，浑身发冷。远处水面亮起一盏菱灯，一叶小舟悠悠划来，船头少女用吴侬软语哼着采菱曲。岸边柳树后，却分明伏着两个黑衣人，手按刀柄，死死盯着那盏灯。',
    options: { A: '伏在篷下，静观其变', B: '学一声水鸟叫，引开黑衣人', C: '跳入湖中，游向小舟报信' },
  },
  {
    state: { location: '太湖畔', time: '丑时', weather: '大雾', physical_state: '略感风寒', inventory: [] },
    scene:
      '大雾忽起，菱灯在雾中晕成一团昏黄。黑衣人低声咒骂，其中一人蹚水而去，另一人仍守在柳树后。你借着雾气挪近了几步，看清他腰间挂着一块铁牌，上书一个“慕”字。小舟上的歌声停了。',
    options: { A: '原地不动，听他们说些什么', B: '摸索岸边，找件趁手之物', C: '扑上去夺他腰间铁牌' },
  },
  {
    state: {
      location: '燕子坞外',
      time: '寅时',
      weather: '晨雾',
      physical_state: '浑身湿透、略感风寒',
      inventory: ['慕字铁牌'],
    },
    scene:
      '你攥着那块冰冷的铁牌，在芦苇荡里一路狂奔，直到天边泛白才敢停下。雾中现出一片水榭楼台，匾上写着“燕子坞”三字。一个挎着竹篮的小丫鬟正在岸边浣纱，抬头见了你，又见了你手里的铁牌，脸色倏地变了。',
    options: { A: '藏起铁牌，装作迷路渔夫', B: '亮出铁牌，试探她的反应', C: '直闯水榭，求见慕容公子' },
  },
]

let cursor = 0

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

// 镜像后端 WorldState.status_bar()：Mock 必须与真实协议同形
const statusBarOf = (s: WorldState) =>
  `【位置：${s.location}】 | 【时辰：${s.time}】 | 【天气：${s.weather}】 | 【状态：${s.physical_state}】 | ` +
  `【行囊：${s.inventory.join(', ') || '空无一物'}】`

const respond = ({ state, scene, options }: Frame): InteractResponse => ({
  ui_status_bar: statusBarOf(state),
  scene_description: scene,
  game_over: false,
  options,
  next_state: state,
})

const execute = (req: InteractRequest): InteractResponse => {
  const state = { ...req.current_state, physical_state: '心脉寸断，气绝身亡' }
  return {
    ui_status_bar: statusBarOf(state),
    scene_description: '你话音未落，雾中一道青影掠过，快得连风声都追不上。你只觉心口一凉，低头看时，一枚铁牌已不偏不倚嵌进了你的胸膛——正是你方才见过的那块。江湖很大，可惜你的故事，到此为止。',
    game_over: true,
    options: null,
    next_state: state,
  }
}

export const mockApi = {
  async newSession(): Promise<NewSessionResponse> {
    await sleep(LATENCY_MS)
    cursor = 0
    return { session_id: crypto.randomUUID(), ...respond(FRAMES[0]) }
  },
  async interact(req: InteractRequest): Promise<InteractResponse> {
    await sleep(LATENCY_MS)
    if (DEATH_WORDS.test(req.action_text)) return execute(req)
    cursor = Math.min(cursor + 1, FRAMES.length - 1)
    return respond(FRAMES[cursor])
  },
}
