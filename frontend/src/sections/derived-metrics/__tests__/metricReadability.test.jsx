import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createTheme,
  decomposeColor,
  getContrastRatio,
} from "@mui/material/styles";
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
// getContrastRatio ignores alpha. Composite the computed sRGB layers first;
// requiring an opaque backing surface prevents a transparent jsdom body from
// silently standing in for the page. This does not measure browser pixels.
function overOpaque(foreground, background) {
  const front = decomposeColor(foreground);
  const back = decomposeColor(background);
  if (
    ![front.type, back.type].every((type) => ["rgb", "rgba"].includes(type)) ||
    (back.values[3] ?? 1) !== 1
  ) {
    throw new Error("Expected sRGB colors on an opaque surface");
  }
  const alpha = front.values[3] ?? 1;
  const channels = front.values
    .slice(0, 3)
    .map(
      (channel, index) => channel * alpha + back.values[index] * (1 - alpha),
    );
  return `rgb(${channels.join(", ")})`;
}
function contrastOnSurface(foreground, background, surface) {
  const paintedBackground = overOpaque(background, surface);
  return getContrastRatio(
    overOpaque(foreground, paintedBackground),
    paintedBackground,
  );
}

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
      <div
        data-testid="metric-surface"
        style={{ backgroundColor: theme.palette.background.paper }}
      >
        <MetricInputs
          projectId="project"
          inputs={inputs}
          readOnly={readOnly}
          onChange={onChange}
        />
      </div>
    </QueryClientProvider>,
    { theme },
  );
}

describe("metrics scoped readability", () => {
  it("composites the ten-percent warning tint before calculating luminance", () => {
    const background = "rgba(245, 158, 11, 0.1)";
    const surface = "rgb(10, 10, 11)";
    expect(overOpaque(background, surface)).toBe("rgb(33.5, 24.8, 11)");
    expect(
      contrastOnSurface("rgb(239, 239, 241)", background, surface),
    ).toBeCloseTo(15.23, 1);
  });
  it("keeps opaque colors unchanged and a zero-alpha tint transparent", () => {
    expect(
      contrastOnSurface("rgb(0, 0, 0)", "rgba(255, 255, 255, 1)", "#000000"),
    ).toBe(21);
    expect(
      contrastOnSurface("rgb(0, 0, 0)", "rgba(245, 158, 11, 0)", "#FFFFFF"),
    ).toBe(21);
  });
  it.each([
    ["equal opaque colors", "#FFFFFF", "#FFFFFF", "#000000"],
    [
      "opaque warning behind light text",
      "#EFEFF1",
      "rgb(245, 158, 11)",
      "#0A0A0B",
    ],
    [
      "transparent foreground",
      "rgba(255, 255, 255, 0.1)",
      "#000000",
      "#FFFFFF",
    ],
  ])(
    "rejects unreadable %s at the unchanged 4.5 threshold",
    (_, text, background, surface) => {
      expect(contrastOnSurface(text, background, surface)).toBeLessThan(4.5);
    },
  );
  it("refuses to certify contrast without an opaque backing surface", () => {
    expect(() =>
      contrastOnSurface(
        "#FFFFFF",
        "rgba(245, 158, 11, 0.1)",
        "rgba(0, 0, 0, 0)",
      ),
    ).toThrow("Expected sRGB colors on an opaque surface");
  });
  it.each(["light", "dark"])(
    "uses semantic warning foreground and background in %s (jsdom, not browser acceptance)",
    async (mode) => {
      mount(mode);
      const note = await screen.findByRole("note");
      expect(note).toHaveClass("MuiAlert-root");
      expect(note).toHaveTextContent(/Select the evaluator again/);
      const surface = screen.getByTestId("metric-surface");
      expect(surface).toHaveStyle({
        backgroundColor: mode === "dark" ? "#0A0A0B" : "#FFFFFF",
      });
      expect(surface).toContainElement(note);
      // There is no intervening paint layer in MetricInputs/EvaluatorBinding.
      for (
        let parent = note.parentElement;
        parent !== surface;
        parent = parent.parentElement
      ) {
        expect(getComputedStyle(parent).backgroundColor).toBe(
          "rgba(0, 0, 0, 0)",
        );
      }
      const style = getComputedStyle(note);
      expect(
        contrastOnSurface(
          style.color,
          style.backgroundColor,
          getComputedStyle(surface).backgroundColor,
        ),
      ).toBeGreaterThanOrEqual(4.5);
      expect(note).toHaveStyle({
        color: palette(mode).text.primary,
        backgroundColor: palette(mode).status.warning.bg,
      });
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
