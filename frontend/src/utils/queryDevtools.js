/**
 * Whether to render the TanStack Query devtools button.
 *
 * It is a development aid only: never in a production build, never on a
 * stack that serves the embedded Omega console (VITE_OMEGA_CONSOLE=1, where
 * the floating button covers the console's tables on a phone), and never when
 * VITE_REACT_QUERY_DEVTOOLS_ENABLED is "false".
 *
 * @param {Record<string, unknown>} env Vite's import.meta.env.
 * @returns {boolean}
 */
export function queryDevtoolsVisible(env) {
  return (
    env?.DEV === true &&
    env.VITE_OMEGA_CONSOLE !== "1" &&
    env.VITE_REACT_QUERY_DEVTOOLS_ENABLED !== "false"
  );
}
