// On the Tracing page the "Send your first trace" card cut its longest
// line (the self-hosted FI_BASE_URL note) at the right edge of the card. The
// code box could scroll sideways, but that scrollbar sits under the last line,
// out of view, so the line just looked cut off. The code now wraps inside the
// box and keeps its indentation; Copy still copies the original text.
import React from "react";
import { render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { describe, expect, it, vi } from "vitest";

import EmptyState from "src/components/empty-state";
import { quickstartTracing } from "src/components/empty-state/quickstarts";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

vi.mock("src/components/iconify", () => ({ default: () => null }));

const theme = createTheme({ palette: palette("light"), typography });

const LONG_LINE =
  '# Self-hosted? Also set os.environ["FI_BASE_URL"] to your collector, for example "http://collector.internal.example:4318", before calling register()';

const SURFACES = [
  ["the tracing quickstart", quickstartTracing("my-project")],
  [
    "a single long line",
    {
      title: "Send your first trace",
      snippets: [{ label: "Python", code: LONG_LINE }],
    },
  ],
];

function renderQuickstart(quickstart) {
  return render(
    <ThemeProvider theme={theme}>
      <EmptyState title="No traces yet" quickstart={quickstart} />
    </ThemeProvider>,
  );
}

describe("quickstart code box layout", () => {
  it.each(SURFACES)(
    "wraps %s inside the code box and keeps the text whole",
    (_, quickstart) => {
      const { container } = renderQuickstart(quickstart);
      const pre = container.querySelector("pre");
      expect(pre).not.toBeNull();
      expect(pre).toHaveStyle({ whiteSpace: "pre-wrap" });
      expect(pre).toHaveStyle({ overflowWrap: "anywhere" });
      // The box still scrolls if a single token cannot break.
      expect(pre).toHaveStyle({ overflowX: "auto" });
      expect(pre.textContent).toBe(quickstart.snippets[0].code);
    },
  );

  it.each(SURFACES)(
    "keeps the Copy code button for %s in the card header, outside the code",
    (_, quickstart) => {
      const { container } = renderQuickstart(quickstart);
      const button = screen.getByRole("button", { name: "Copy code" });
      expect(button).toBeInTheDocument();
      expect(container.querySelector("pre").contains(button)).toBe(false);
    },
  );
});
