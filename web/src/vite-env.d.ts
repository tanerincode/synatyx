/// <reference types="vite/client" />
interface ImportMetaEnv {
  readonly VITE_MOCK_ALL?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}
