/// <reference types="vite/client" />

/** Deployment values injected by the runtime configuration bootstrap. */
interface Window {
  __FUTURE_AGI_CONFIG__?: Record<string, string | undefined> & {
    /** Public AG Grid licence; a nonblank runtime value overrides the build. */
    VITE_AG_GRID_LICENSE_KEY?: string;
    /** Absolute http(s) URL for the optional direct CLI deploy command. */
    VITE_OMEGA_DEPLOY_CONTROL_PLANE?: string;
  };
}
