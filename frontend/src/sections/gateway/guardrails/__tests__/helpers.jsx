// Test helpers for the Guardrails page: a render with the Future AGI theme,
// React Query and a router, and an AG Grid stand-in that renders each row's
// cells through the real column definitions (so cell renderers, clicks and
// switches can be exercised without the grid's canvas).
import React from "react";
import PropTypes from "prop-types";
import { MemoryRouter } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { render } from "@testing-library/react";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { ThemeProvider } from "@mui/material/styles";
import { faTestTheme } from "../../__tests__/faTheme";

export function renderPage(ui, { route = "/", mode = "light" } = {}) {
  const client = new QueryClient({
    mutationCache: new MutationCache({ onError: () => {} }),
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  function Wrapper({ children }) {
    return (
      <HelmetProvider>
        <QueryClientProvider client={client}>
          <ThemeProvider theme={faTestTheme(mode)}>
            <MemoryRouter initialEntries={[route]}>{children}</MemoryRouter>
          </ThemeProvider>
        </QueryClientProvider>
      </HelmetProvider>
    );
  }
  Wrapper.propTypes = { children: PropTypes.node };
  return render(ui, { wrapper: Wrapper });
}

function cellValue(col, data) {
  if (col.valueGetter) return col.valueGetter({ data });
  return col.field ? data[col.field] : undefined;
}

export function MockAgGrid({ rowData, columnDefs, onRowClicked, getRowId }) {
  if (!rowData) return <div data-testid="grid-loading" />;
  return (
    <table data-testid="ag-grid">
      <thead>
        <tr>
          {columnDefs.map((col) => (
            <th
              key={col.colId || col.field || col.headerName}
              data-width={col.width}
              data-flex={col.flex}
            >
              {col.headerName}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rowData.map((data, index) => (
          <tr
            key={getRowId ? getRowId({ data }) : index}
            data-testid={`row-${getRowId ? getRowId({ data }) : index}`}
            onClick={(event) => onRowClicked?.({ data, event })}
          >
            {columnDefs.map((col) => {
              const value = cellValue(col, data);
              const Renderer = col.cellRenderer;
              const shown = col.valueFormatter
                ? col.valueFormatter({ value, data })
                : value;
              return (
                <td key={col.colId || col.field || col.headerName}>
                  {Renderer ? (
                    <Renderer data={data} value={value} />
                  ) : (
                    String(shown ?? "")
                  )}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

MockAgGrid.propTypes = {
  rowData: PropTypes.array,
  columnDefs: PropTypes.array,
  onRowClicked: PropTypes.func,
  getRowId: PropTypes.func,
};

// The demo org's guardrail section (GET org-configs/active/, trimmed).
export const DEMO_GUARDRAILS = {
  rules: [
    {
      mode: "sync",
      name: "pii-detection",
      stage: "pre",
      action: "block",
      config: { entities: ["email", "phone", "ssn", "credit_card"] },
      enabled: true,
      threshold: 0.5,
    },
    {
      mode: "sync",
      name: "keyword-blocklist",
      stage: "pre",
      action: "block",
      config: { words: ["project-zeus", "internal only"], whole_word: false },
      enabled: true,
      threshold: 0.5,
    },
    {
      mode: "sync",
      name: "prompt-injection",
      stage: "pre",
      action: "warn",
      config: {},
      enabled: true,
      threshold: 0.6,
    },
    {
      mode: "sync",
      name: "secret-detection",
      stage: "pre",
      action: "block",
      config: {},
      enabled: false,
      threshold: 0.5,
    },
  ],
};

export const DEMO_RULE_STATS = {
  rules: [
    {
      rule: "pii-detection",
      trigger_count: 24,
      block_count: 24,
      warn_count: 0,
    },
    {
      rule: "keyword-blocklist",
      trigger_count: 12,
      block_count: 12,
      warn_count: 0,
    },
    {
      rule: "secret-detection",
      trigger_count: 6,
      block_count: 6,
      warn_count: 0,
    },
    {
      rule: "prompt-injection",
      trigger_count: 1,
      block_count: 0,
      warn_count: 1,
    },
  ],
};

export const DEMO_OVERVIEW = {
  total_requests: 592,
  guardrail_triggered: 43,
  trigger_rate: 7.26,
  block_count: 42,
  warn_count: 1,
  block_rate: 7.09,
  warn_rate: 0.17,
  avg_guardrail_latency_ms: 0.0,
};

export const DEMO_FEEDBACK_SUMMARY = [
  { check_name: "keyword-blocklist", total_feedback: 5, correct_rate: 80.0 },
  { check_name: "pii-detection", total_feedback: 4, correct_rate: 75.0 },
];

// Header label widths in px, measured in Chromium at 1440 px on this theme
//. An AG Grid header keeps
// 56 px of its cell for padding and the sort and menu icons, so a column
// whose label is wider than its width minus that ends in an ellipsis.
export const HEADER_TEXT_PX = {
  Reviews: 56,
  Correct: 51,
  "False positive": 91,
  "False negative": 96,
  Unsure: 48,
  Accuracy: 64,
  Matches: 58,
  Blocked: 54,
  Warned: 52,
  "Share of matches": 117,
};
export const HEADER_CHROME_PX = 56;

/** Every fixed-width column header in the grid, with the room it needs. */
export function fixedHeaders(grid) {
  return [...grid.querySelectorAll("th")]
    .filter((th) => th.dataset.flex === "0" && th.dataset.width)
    .map((th) => ({
      label: th.textContent,
      width: Number(th.dataset.width),
      needs: (HEADER_TEXT_PX[th.textContent] ?? Infinity) + HEADER_CHROME_PX,
    }));
}
