import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { FIX_RUN_FIXTURES } from "src/api/simulate-environments/__tests__/fixtures/fixRuns";

vi.mock("src/utils/axios", async (importOriginal) => {
  const { withFakeFixRunsApi } = await import(
    "src/api/simulate-environments/__tests__/fixtures/fixRunsApi"
  );
  return withFakeFixRunsApi(await importOriginal());
});

const { default: FixVerdict } = await import("../FixVerdict");

const renderVerdict = (extra) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <FixVerdict
          fix={{
            ...structuredClone(FIX_RUN_FIXTURES.find((f) => f.id === "fix-ready-archive")),
            environment_id: "env-1",
            ...extra,
          }}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );

describe("platform evals the winner does worse on", () => {
  it("warns on the result with each eval's rate against the base version's", () => {
    renderVerdict({
      eval_warnings: [
        { name: "Politeness", pass_rate: 0.6, base_pass_rate: 0.85, trials: 6, base_trials: 6 },
        { name: "Tone", pass_rate: 0.5, base_pass_rate: 0.75, trials: 4, base_trials: 8 },
      ],
    });
    expect(
      screen.getByText("Does worse on 2 platform evals than the current version. The verdict does not count them."),
    ).toBeInTheDocument();
    expect(screen.getByText("Politeness")).toBeInTheDocument();
    expect(screen.getByText("60% vs 85% · 6 trials vs 6")).toBeInTheDocument();
    expect(screen.getByText("50% vs 75% · 4 trials vs 8")).toBeInTheDocument();
    expect(screen.getByText(/failing scenarios on the held-back set|Held-back pass rate/)).toBeInTheDocument();
  });

  it("says nothing when there are none", () => {
    renderVerdict({ eval_warnings: [] });
    expect(screen.queryByText(/platform eval/)).toBeNull();
  });

  it("says nothing when the read leaves them out", () => {
    renderVerdict({ eval_warnings: undefined });
    expect(screen.queryByText(/platform eval/)).toBeNull();
  });
});
