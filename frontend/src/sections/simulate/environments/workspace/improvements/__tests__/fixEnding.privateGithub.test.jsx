import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";

import { palette } from "src/theme/palette";
import { fixtureFixRun } from "src/api/simulate-environments/__tests__/fixtures/fixRuns";

vi.mock("src/utils/axios", async (importOriginal) => {
  const { withFakeFixRunsApi } = await import(
    "src/api/simulate-environments/__tests__/fixtures/fixRunsApi"
  );
  return withFakeFixRunsApi(await importOriginal());
});

const { fixEnding, headline } = await import("../fixRunView");
const { default: FixFinishedView } = await import("../FixFinishedView");

const REFUSED = "github_private_unavailable";
const SENTENCE =
  "Private GitHub repositories are not available on Future AGI cloud. Upload the code to keep using this environment.";
// The words the repository read refuses with, written for a new environment.
const CREATE_PANEL_SENTENCE =
  "Private GitHub repositories are not available on Future AGI cloud. Use a public repository or upload the code.";

// A failed fix with no trials, which the backend still offers to retry.
const failedWith = (failure) => ({
  ...structuredClone(fixtureFixRun("fix-failed")),
  environment_id: "env-1",
  failure,
});

const appTheme = createTheme({ palette: palette("light") });
function renderFinished(fix) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <ThemeProvider theme={appTheme}>
      <QueryClientProvider client={client}>
        <SnackbarProvider>
          <MemoryRouter>
            <FixFinishedView
              fix={fix}
              tab="trials"
              onTab={vi.fn()}
              renderTab={() => null}
            />
          </MemoryRouter>
        </SnackbarProvider>
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

const refusals = [
  [
    "stored with the words for a new environment",
    { message: CREATE_PANEL_SENTENCE },
  ],
  ["stored with no words", {}],
  ["stored with the sentence itself", { message: SENTENCE }],
];

describe("a fix that could not read its private GitHub repository", () => {
  it.each(refusals)(
    "ends in the environment's one sentence when %s",
    (_name, words) => {
      const fix = failedWith({ code: REFUSED, ...words });
      expect(fixEnding(fix)).toEqual({ text: SENTENCE, details: null });
      expect(headline(fix)).toBe(SENTENCE);
    },
  );

  it.each(refusals)(
    "shows the sentence on its page, never contact support or details, when %s",
    (_name, words) => {
      renderFinished(failedWith({ code: REFUSED, ...words }));
      expect(screen.getByText(SENTENCE)).toBeInTheDocument();
      expect(screen.queryByText(/contact support/i)).toBeNull();
      expect(screen.queryByText(/stopped unexpectedly/)).toBeNull();
      expect(screen.queryByRole("button", { name: "Details" })).toBeNull();
    },
  );
});

describe("any other failed fix", () => {
  it("still leads a fix the engine stopped with what to do next", () => {
    const fix = failedWith({
      code: "fix_engine_failed",
      message: "the improvement engine hit an error it could not recover from",
    });
    expect(fixEnding(fix)).toEqual({
      text: "The improvement stopped unexpectedly. Retry it, or contact support with improvement ID fix-failed.",
      details: null,
    });
  });

  it("still reads a failure the user can fix in its own words", () => {
    const fix = failedWith({
      code: "usage_limit",
      message: "AI credits are paused by your budget",
    });
    expect(fixEnding(fix)).toEqual({
      text: "AI credits are paused by your budget",
      details: null,
    });
  });
});
