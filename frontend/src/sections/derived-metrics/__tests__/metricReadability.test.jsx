import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createTheme, getContrastRatio } from "@mui/material/styles";
import { cleanup, fireEvent, render, screen } from "src/utils/test-utils";
import { palette } from "src/theme/palette";
import { alert } from "src/theme/overrides/components/alert";
import MetricInputs from "../MetricInputs";

const current = {
  id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  name: "Saved evaluator",
  config_hash: "a".repeat(64),
};
vi.mock("src/utils/axios", () => ({
  default: {
    get: async () => ({
      data: {
        results: [
          {
            id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            name: "Saved evaluator",
            config_hash: "a".repeat(64),
          },
        ],
      },
    }),
  },
  endpoints: { derivedMetrics: { evaluators: "/evaluators/" } },
}));
let client;
afterEach(() => {
  cleanup();
  client?.clear();
});
function mount(mode, readOnly = false, onChange = vi.fn()) {
  const base = createTheme({ palette: palette(mode) });
  const theme = createTheme(base, { components: alert(base) });
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const inputs = [
    {
      alias: "resolved",
      source: "eval",
      config_id: current.id,
      config_hash: "b".repeat(64),
      reducer: "latest",
    },
  ];
  render(
    <QueryClientProvider client={client}>
      <MetricInputs
        projectId="project"
        inputs={inputs}
        readOnly={readOnly}
        onChange={onChange}
      />
    </QueryClientProvider>,
    { theme },
  );
}

describe("metrics scoped readability", () => {
  it.each(["light", "dark"])(
    "uses semantic warning foreground and background in %s (jsdom, not browser acceptance)",
    async (mode) => {
      mount(mode);
      const note = await screen.findByRole("note");
      expect(note).toHaveClass("MuiAlert-root");
      expect(note).toHaveTextContent(/Select the evaluator again/);
      const style = getComputedStyle(note);
      expect(
        getContrastRatio(style.color, style.backgroundColor),
      ).toBeGreaterThanOrEqual(4.5);
    },
  );
  it("Viewer evaluator values are readable without a mutation handler or keyboard picker", async () => {
    const change = vi.fn();
    mount("light", true, change);
    const evaluator = await screen.findByDisplayValue("Saved evaluator");
    expect(evaluator).not.toBeDisabled();
    expect(evaluator).toHaveAttribute("readonly");
    evaluator.focus();
    expect(evaluator).toHaveFocus();
    fireEvent.keyDown(evaluator, { key: "ArrowDown" });
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Advanced: show/ }));
    const hash = screen.getByLabelText("Input 1 evaluator hash");
    expect(hash).toHaveAttribute("readonly");
    fireEvent.change(hash, { target: { value: "c".repeat(64) } });
    fireEvent.change(screen.getByLabelText("Input 1 alias"), {
      target: { value: "mutated" },
    });
    expect(screen.getByLabelText("Input 1 source")).toHaveValue(
      "Evaluation result",
    );
    expect(change).not.toHaveBeenCalled();
    expect(hash).toHaveValue("b".repeat(64));
  });
});
