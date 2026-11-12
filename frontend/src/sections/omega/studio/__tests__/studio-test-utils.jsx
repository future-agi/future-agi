/* eslint-disable react-refresh/only-export-components */
import React from "react";
import { act, render, screen } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";

import { resetOmegaConnectForTests } from "src/sections/omega/embed/connect-intake";

import StudioPage from "../StudioPage";

export const SERVER = "http://127.0.0.1:4191";
// Deterministic, format-valid test fixture; not a generated access token.
export const TOKEN = "studio-fixture-".padEnd(43, "0");
export const HELLO = { type: "embed.hello", protocol: "omega-embed/1" };

// jsdom's own Web Storage. On Node 25+ the runtime's experimental Web Storage
// shadows it (window.localStorage reads undefined without
// --localstorage-file), so the page under test is given jsdom's, as a
// browser would have.
const jsdomWindow = () => globalThis.jsdom?.window ?? window;
export const storages = () => [
  jsdomWindow().localStorage,
  jsdomWindow().sessionStorage,
];

function installJsdomStorage() {
  for (const kind of ["localStorage", "sessionStorage"]) {
    const real = jsdomWindow()[kind];
    let current;
    try {
      current = window[kind];
    } catch {
      current = undefined;
    }
    if (real && current !== real) {
      Object.defineProperty(window, kind, {
        configurable: true,
        get: () => real,
      });
    }
  }
}
export const allStorageText = () =>
  storages()
    .map((storage) => JSON.stringify({ ...storage }))
    .join("\n");

// What the first script in index.html leaves behind for a link it took out
// of the address bar: a non-enumerable global that answers once.
export function planLink(url = SERVER, token = TOKEN) {
  let held = { url, token };
  Object.defineProperty(window, "__omegaConnect", {
    configurable: true,
    enumerable: false,
    get() {
      const value = held;
      held = null;
      return value;
    },
  });
}

export function resetStudioTestState() {
  installJsdomStorage();
  delete window.__omegaConnect;
  resetOmegaConnectForTests();
  storages().forEach((storage) => storage.clear());
}

export let probe = {};
function Probe() {
  probe = { location: useLocation(), navigate: useNavigate() };
  return null;
}

// The same two routes the shell registers for the Studio tab. `strict` renders
// them under React.StrictMode, which runs every useState initializer and
// mount effect twice, as development builds do.
export function renderStudio({
  path = "/dashboard/studio",
  mode = "light",
  strict = false,
} = {}) {
  const tree = (
    <HelmetProvider>
      <ThemeProvider theme={createTheme({ palette: { mode } })}>
        <MemoryRouter initialEntries={[path]}>
          <Probe />
          <Routes>
            <Route path="/dashboard/studio" element={<StudioPage />} />
            <Route path="/dashboard/studio/*" element={<StudioPage />} />
            <Route path="*" element={null} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </HelmetProvider>
  );
  return render(strict ? <React.StrictMode>{tree}</React.StrictMode> : tree);
}

export const frame = () => screen.getByTestId("omega-embed-frame");

export function spyOnFrame(element = frame()) {
  const posted = [];
  element.contentWindow.postMessage = (message, targetOrigin) =>
    posted.push({ message, targetOrigin });
  return posted;
}

export const fromChild = (element, data, origin = SERVER) =>
  act(async () => {
    window.dispatchEvent(
      new MessageEvent("message", {
        data,
        origin,
        source: element.contentWindow,
      }),
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
