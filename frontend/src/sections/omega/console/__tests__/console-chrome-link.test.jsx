import React from "react";
import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { describe, expect, it, vi } from "vitest";

import ConsoleFramePage from "../ConsoleFramePage";
import { hasShellChrome, useConsoleChrome } from "../console-chrome-link";

// The Console names its document in every hello and gate and uses only a key
// that echoes it (RL 1275A); the Console frame requires it (requireDoc).
const DOC = "AAECAwQFBgcICQoLDA0ODw";

// The shell lane's chrome hook, when this build has it (after the integrator
// merges lane shell). Both branches are checked, so this file passes on the
// lane branch alone and proves the real wiring once the shell is present.
const shellModule = Object.values(
  /** @type {Record<string, typeof import("../../shell/console-chrome")>} */ (
    import.meta.glob("/src/sections/omega/shell/console-chrome.js", {
      eager: true,
    })
  ),
)[0];

describe("console chrome link", () => {
  it("links the shell's hook exactly when the shell's file is in the build", () => {
    expect(hasShellChrome).toBe(Boolean(shellModule));
    if (shellModule) {
      expect(useConsoleChrome).toBe(shellModule.useOmegaConsoleChrome);
    }
  });

  it.skipIf(Boolean(shellModule))(
    "is a harmless no-op without the shell",
    () => {
      const { result } = renderHook(() => useConsoleChrome());
      expect(result.current.frameShown).toBe(false);
      expect(() => result.current.setFrameShown(true)).not.toThrow();
    },
  );

  it.runIf(Boolean(shellModule))(
    "tells the shell the frame is shown once the Console confirms its key",
    async () => {
      const { OmegaConsoleChromeContext } = shellModule;
      const setFrameShown = vi.fn();
      render(
        <HelmetProvider>
          <OmegaConsoleChromeContext.Provider
            value={{ frameShown: false, setFrameShown }}
          >
            <MemoryRouter initialEntries={["/dashboard/console"]}>
              <Routes>
                <Route
                  path="/dashboard/console/*"
                  element={
                    <ConsoleFramePage
                      mintAssertion={async () => ({
                        token: "omega_delegate_x",
                      })}
                    />
                  }
                />
              </Routes>
            </MemoryRouter>
          </OmegaConsoleChromeContext.Provider>
        </HelmetProvider>,
      );
      const frame = screen.getByTestId("omega-embed-frame");
      act(() => fireEvent.load(frame));
      const postMessage = vi
        .spyOn(frame.contentWindow, "postMessage")
        .mockImplementation(() => {});
      expect(setFrameShown).toHaveBeenLastCalledWith(false);

      const fromConsole = (data) =>
        act(async () => {
          window.dispatchEvent(
            new MessageEvent("message", {
              data,
              origin: window.location.origin,
              source: frame.contentWindow,
            }),
          );
          await new Promise((resolve) => setTimeout(resolve, 0));
        });

      await fromConsole({
        type: "embed.hello",
        protocol: "omega-embed/1",
        doc: DOC,
      });
      expect(postMessage).toHaveBeenCalledWith(
        { type: "embed.key", token: "omega_delegate_x", doc: DOC },
        window.location.origin,
      );
      // Holding a key is not enough: the shell's chrome stays until the
      // Console confirms the key it was handed.
      expect(setFrameShown).toHaveBeenLastCalledWith(false);
      expect(setFrameShown).not.toHaveBeenCalledWith(true);

      await fromConsole({ type: "embed.gate", state: "ready", doc: DOC });
      expect(setFrameShown).toHaveBeenLastCalledWith(true);
    },
  );
});
