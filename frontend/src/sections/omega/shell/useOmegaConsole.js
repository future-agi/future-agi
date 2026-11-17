import { isOmegaConsoleEnabled } from "./flags";
import { GATEWAY_STATE, useGatewayProbeState } from "./gatewayProbe";

/**
 * The Omega switch for a component that must follow the gateway probe
 * (flags.js, gatewayProbe.js): the router's table, the sidebar, Settings and
 * the dashboard shell. Re-renders the caller when the probe settles.
 *   enabled  isOmegaConsoleEnabled(), read again on every render;
 *   pending  the probe is still out (only with VITE_OMEGA_CONSOLE unset):
 *            Omega paths keep their URL and show the loading screen.
 * Kept apart from flags.js so a test that mocks flags still gets the hook.
 */
export function useOmegaConsole() {
  const probe = useGatewayProbeState();
  return {
    enabled: isOmegaConsoleEnabled(),
    pending: probe === GATEWAY_STATE.CHECKING,
  };
}

export default useOmegaConsole;
