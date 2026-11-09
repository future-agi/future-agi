/* eslint-disable react-refresh/only-export-components */
import React from "react";
import PropTypes from "prop-types";
import { HelmetProvider } from "react-helmet-async";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { QueryClientProvider } from "@tanstack/react-query";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { shadows } from "src/theme/shadows";
import { customShadows } from "src/theme/custom-shadows";

import { rolloutQueryClient } from "./rollout-fixtures";

// Theme and head providers for shell tests. Mirrors src/utils/test-utils.jsx
// but leaves the router to each test, since these tests drive real route
// tables through createMemoryRouter.
export function makeTestTheme(mode = "light") {
  return createTheme({
    palette: palette(mode),
    typography,
    shadows: shadows(mode),
    customShadows: customShadows(mode),
  });
}

// `queryClient` holds the rollout answer (rollout-fixtures.js); by default
// every Omega product is admitted, so tests of the shell see the whole shell.
export function ShellTestProviders({ children, mode = "light", queryClient }) {
  const [client] = React.useState(() => queryClient || rolloutQueryClient());
  return (
    <HelmetProvider>
      <QueryClientProvider client={client}>
        <ThemeProvider theme={makeTestTheme(mode)}>{children}</ThemeProvider>
      </QueryClientProvider>
    </HelmetProvider>
  );
}

ShellTestProviders.propTypes = {
  children: PropTypes.node,
  mode: PropTypes.oneOf(["light", "dark"]),
  queryClient: PropTypes.object,
};

// window.matchMedia that answers min-width / max-width queries for a given
// viewport width, so MUI's useMediaQuery sees a real breakpoint.
export function installViewport(width) {
  const evaluate = (query) => {
    const min = /min-width:\s*([\d.]+)px/.exec(query);
    const max = /max-width:\s*([\d.]+)px/.exec(query);
    if (min && width < Number(min[1])) return false;
    if (max && width > Number(max[1])) return false;
    return Boolean(min || max);
  };
  window.matchMedia = (query) => ({
    matches: evaluate(query),
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  });
}

// Node 25+ ships its own global localStorage, which is undefined without
// --localstorage-file and hides jsdom's. CI runs Node 22 (.nvmrc), where
// jsdom's storage is used; this only fills the gap on newer local Nodes.
export function ensureWebStorage() {
  for (const name of ["localStorage", "sessionStorage"]) {
    let usable = false;
    try {
      usable = typeof globalThis[name]?.getItem === "function";
    } catch {
      usable = false;
    }
    if (usable) continue;
    const store = new Map();
    Object.defineProperty(globalThis, name, {
      configurable: true,
      value: {
        getItem: (key) => (store.has(key) ? store.get(key) : null),
        setItem: (key, value) => store.set(key, String(value)),
        removeItem: (key) => store.delete(key),
        clear: () => store.clear(),
        key: (index) => Array.from(store.keys())[index] ?? null,
        get length() {
          return store.size;
        },
      },
    });
  }
}
