/**
 * [INPUT]: 依赖 tsconfig 引入的 vite/client 类型（ImportMetaEnv 接口合并）
 * [OUTPUT]: 对外提供 import.meta.env.VITE_API_BASE 的类型
 * [POS]: frontend 的环境变量声明；只剩后端地址一个开关——前端没有 Mock 模式，离线体验由后端 LLM_PROVIDER=mock 提供
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
 */
interface ImportMetaEnv {
  /** 后端地址前缀；缺省为空串，走 Vite 开发代理的同源 /api */
  readonly VITE_API_BASE?: string
}
