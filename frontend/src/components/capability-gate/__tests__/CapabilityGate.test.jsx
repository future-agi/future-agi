import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { palette } from "src/theme/palette";

// What /api/capabilities/ answers: an open-source server denies Instant eval
// and Insights (oss_locked, as Error Feed).
const capabilities = vi.hoisted(() => ({ features: {} }));

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: {
      ...actual.default,
      get: (url) =>
        url.endsWith("/api/capabilities/")
          ? Promise.resolve({ data: { features: capabilities.features } })
          : Promise.reject(new Error(`unexpected ${url}`)),
    },
  };
});

import CapabilityGate from "../CapabilityGate";
import EvaluateMenu from "src/sections/projects/LLMTracing/InstantEval/EvaluateMenu";
import { CAPABILITY } from "src/hooks/useCapabilities";
import {
  askLink,
  insightsReportLink,
} from "src/sections/projects/observeHandoff";

const PROJECT = "p1";
const OSS = {
  instant_eval: { allowed: false, reason_code: null },
  insights: { allowed: false, reason_code: null },
};
const LICENSED = {
  instant_eval: { allowed: true },
  insights: { allowed: true },
};

// A view's toolbar: Evaluate hands its rows over, as the Observe views do.
function Toolbar() {
  const navigate = useNavigate();
  return (
    <EvaluateMenu
      unit="sessions"
      onAsk={() => navigate(askLink(PROJECT, { unit: "sessions" }))}
      onFindPatterns={() =>
        navigate(insightsReportLink(PROJECT, { unit: "sessions" }))
      }
      onAddEvaluators={() => {}}
    />
  );
}

function renderAt(path) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const Shell = ({ children }) => (
    <QueryClientProvider client={client}>
      <ThemeProvider theme={createTheme({ palette: palette("light") })}>
        {children}
      </ThemeProvider>
    </QueryClientProvider>
  );
  Shell.propTypes = { children: PropTypes.node };
  return render(
    <Shell>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/dashboard/observe/:observeId/sessions"
            element={<Toolbar />}
          />
          {/* As routes/sections/dashboard.jsx gates the two pages. */}
          <Route
            path="/dashboard/observe/:observeId/ask"
            element={
              <CapabilityGate feature={CAPABILITY.INSTANT_EVAL}>
                <div>The Ask page</div>
              </CapabilityGate>
            }
          />
          <Route
            path="/dashboard/observe/:observeId/insights"
            element={
              <CapabilityGate feature={CAPABILITY.INSIGHTS}>
                <div>The Insights page</div>
              </CapabilityGate>
            }
          />
        </Routes>
      </MemoryRouter>
    </Shell>,
  );
}

beforeEach(() => {
  capabilities.features = OSS;
  globalThis.sessionStorage?.clear?.();
});

describe("Instant eval and Insights on open source", () => {
  it.each([
    ["ask", "Upgrade to use Instant eval", "The Ask page"],
    ["insights", "Upgrade to use Insights", "The Insights page"],
  ])("gates /%s instead of opening the page", async (route, title, page) => {
    renderAt(`/dashboard/observe/${PROJECT}/${route}`);
    expect(await screen.findByText(title)).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Upgrade to EE license key" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(page)).toBeNull();
  });

  it("names Jev evaluators as included on the Instant eval gate", async () => {
    renderAt(`/dashboard/observe/${PROJECT}/ask`);
    expect(
      await screen.findByText(/Jev evaluators come with every edition/),
    ).toBeInTheDocument();
  });

  it.each([
    [/Ask a question/, "Upgrade to use Instant eval"],
    [/Find patterns/, "Upgrade to use Insights"],
  ])(
    "marks %s in the Evaluate menu and opens its gate",
    async (name, title) => {
      renderAt(`/dashboard/observe/${PROJECT}/sessions`);
      fireEvent.click(screen.getByRole("button", { name: "Evaluate" }));
      const item = screen.getByRole("menuitem", { name });
      expect(await within(item).findByText("Enterprise")).toBeInTheDocument();
      expect(
        within(
          screen.getByRole("menuitem", { name: /Add evaluators/ }),
        ).queryByText("Enterprise"),
      ).toBeNull();
      fireEvent.click(item);
      expect(await screen.findByText(title)).toBeInTheDocument();
    },
  );
});

describe("Instant eval and Insights with a license", () => {
  beforeEach(() => {
    capabilities.features = LICENSED;
  });

  it.each([
    ["ask", "The Ask page"],
    ["insights", "The Insights page"],
  ])("opens /%s as before", async (route, page) => {
    renderAt(`/dashboard/observe/${PROJECT}/${route}`);
    expect(await screen.findByText(page)).toBeInTheDocument();
    expect(screen.queryByText(/Upgrade to use/)).toBeNull();
  });

  it("marks nothing in the Evaluate menu and hands the rows over", async () => {
    renderAt(`/dashboard/observe/${PROJECT}/sessions`);
    fireEvent.click(screen.getByRole("button", { name: "Evaluate" }));
    await screen.findAllByRole("menuitem");
    expect(screen.queryByText("Enterprise")).toBeNull();
    fireEvent.click(screen.getByRole("menuitem", { name: /Ask a question/ }));
    expect(await screen.findByText("The Ask page")).toBeInTheDocument();
  });
});
