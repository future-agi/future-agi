import React from "react";
import { createTheme } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { render, screen } from "src/utils/test-utils";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { shadows } from "src/theme/shadows";
import { customShadows } from "src/theme/custom-shadows";
import { dsShape } from "src/theme/ds-tokens";
import { componentsOverrides } from "src/theme/overrides";

import GatewayUsageSplit from "../GatewayUsageSplit";

// Finding rank 11: on Settings > Usage Summary with managed spend unavailable,
// the "Retry managed spend" text button kept the theme's text-button side
// padding, so its label sat 8px right of the "Managed spend unavailable"
// line it belongs to.

const state = vi.hoisted(() => ({ statementError: true }));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  const gw = actual.endpoints.gateway;
  return {
    ...actual,
    default: {
      ...actual.default,
      get: vi.fn(async (url) => {
        if (url === gw.managedCreditStatement) {
          if (state.statementError) throw new Error("statement_unavailable");
          return { data: { result: { spend_by_model: [] } } };
        }
        if (url === gw.analyticsCost) {
          return {
            data: {
              result: {
                group_by: "billing_mode",
                breakdown: [{ name: "Own key", total_cost: "0" }],
              },
            },
          };
        }
        throw new Error(`unmocked GET ${url}`);
      }),
    },
  };
});

function appTheme(mode) {
  const theme = createTheme({
    palette: palette(mode),
    customShadows: customShadows(mode),
    shadows: shadows(mode),
    shape: dsShape,
    typography,
  });
  theme.components = componentsOverrides(theme);
  return theme;
}

function renderSplit(mode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <GatewayUsageSplit now={new Date("2026-11-10T12:00:00Z")} />
    </QueryClientProvider>,
    { theme: appTheme(mode) },
  );
}

describe("Usage Summary managed spend unavailable", () => {
  beforeEach(() => {
    state.statementError = true;
  });

  it.each(["light", "dark"])(
    "lines Retry managed spend up with the Managed spend unavailable line (%s)",
    async (mode) => {
      renderSplit(mode);

      expect(
        await screen.findByText("Managed spend unavailable"),
      ).toBeInTheDocument();
      const retry = screen.getByRole("button", {
        name: "Retry managed spend",
      });
      const style = getComputedStyle(retry);
      expect(style.paddingLeft).toMatch(/^0(px)?$/);
    },
  );
});
