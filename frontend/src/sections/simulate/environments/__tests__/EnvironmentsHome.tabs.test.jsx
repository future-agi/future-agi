// At 1280 and 1440 px the Environments page drew
// left and right scroll arrows around its three tabs although they fit. The
// app's Tabs default to the scrollable variant, whose arrows follow an
// IntersectionObserver report on the first and last tab; a stale report
// left them up. The three tabs (about 330 px) fit any supported window, so
// the strip does not scroll and draws no arrows. jsdom lays nothing out:
// the pixel proof is a browser walk.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { customShadows } from "src/theme/custom-shadows";
import { dsShape } from "src/theme/ds-tokens";
import { componentsOverrides } from "src/theme/overrides";
import { resetEnvironmentsStore } from "../store/useEnvironmentsStore";

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/sections/persona/PersonaListView", () => ({
  default: () => <div>persona-list-stub</div>,
}));
vi.mock("src/api/simulate-environments/harnessEnvironments", () => ({
  listHarnessEnvironments: vi.fn(() =>
    Promise.resolve({ count: 0, next: null, previous: null, results: [] }),
  ),
  deleteHarnessEnvironment: vi.fn(),
}));
vi.mock("src/utils/Mixpanel", async (importOriginal) => ({
  ...(await importOriginal()),
  trackEvent: vi.fn(),
}));

const { default: EnvironmentsHome } = await import("../EnvironmentsHome");

// The app's theme, built as theme/index.jsx builds it: its Tabs default to
// the scrollable variant.
const appTheme = (mode) => {
  const theme = createTheme({
    palette: palette(mode),
    typography,
    customShadows: customShadows(mode),
    shape: dsShape,
  });
  theme.components = componentsOverrides(theme);
  return theme;
};

// setupTests stubs the observer as a no-op; report "first and last tab not
// fully in view", the stale report that drew the arrows.
let SavedObserver;
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});
beforeEach(() => {
  resetEnvironmentsStore();
  SavedObserver = globalThis.IntersectionObserver;
  globalThis.IntersectionObserver = class {
    constructor(callback) {
      this.callback = callback;
    }

    observe() {
      this.callback([{ isIntersecting: false }]);
    }

    unobserve() {}

    disconnect() {}
  };
});
afterEach(() => {
  globalThis.IntersectionObserver = SavedObserver;
});

describe("Environments page tab rail", () => {
  it.each(["light", "dark"])(
    "shows its three tabs with no scroll arrows (%s)",
    async (mode) => {
      render(
        <MemoryRouter initialEntries={["/dashboard/simulate/environments"]}>
          <QueryClientProvider client={new QueryClient()}>
            <ThemeProvider theme={appTheme(mode)}>
              <EnvironmentsHome />
            </ThemeProvider>
          </QueryClientProvider>
        </MemoryRouter>,
      );
      const build = await screen.findByRole("tab", {
        name: "Build environment",
      });
      const rail = build.closest(".MuiTabs-root");
      ["My Environments", "Personas"].forEach((name) =>
        expect(rail.contains(screen.getByRole("tab", { name }))).toBe(true),
      );
      expect(rail.querySelector(".MuiTabs-scrollButtons")).toBeNull();
      expect(rail.querySelector(".MuiTabs-scroller")).toHaveClass(
        "MuiTabs-fixed",
      );
    },
  );
});
