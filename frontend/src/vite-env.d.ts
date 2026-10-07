// 声明本项目使用的 VITE_* 环境变量，让 import.meta.env 具备类型
interface ImportMetaEnv {
  readonly VITE_USE_MOCK?: string
  readonly VITE_API_BASE?: string
}
