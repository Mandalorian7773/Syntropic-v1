/// <reference types="vite/client" />
//
// Only one env var exists, and it is the backend switch. Declared explicitly
// so `import.meta.env.VITE_API_BASE` is typed rather than `any`.
interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}
