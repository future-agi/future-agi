/// <reference types="vite/client" />

/** Deployment values injected by the runtime configuration bootstrap. */
interface Window {
  __FUTURE_AGI_CONFIG__?: Record<string, string | undefined>;
}
