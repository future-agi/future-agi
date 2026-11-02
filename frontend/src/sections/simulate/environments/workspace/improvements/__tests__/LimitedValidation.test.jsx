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
const { listResult } = await import("../fixRunView");

const byId = (id, extra = {}) => ({
  ...structuredClone(FIX_RUN_FIXTURES.find((f) => f.id === id)),
  environment_id: "env-1",
  ...extra,
});

const renderVerdict = (fix) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <FixVerdict fix={fix} />
      </MemoryRouter>
    </QueryClientProvider>,
  );

describe("a fix with limited validation", () => {
  it("labels the result and counts the held-back scenarios it fixed", async () => {
    renderVerdict(byId("fix-ready-archive", { limited_validation: true }));
    expect(screen.getByText("Limited validation")).toBeInTheDocument();
    expect(
      await screen.findByText(
        "Too few to tell · Fixed 1 of 2 failing scenarios it never saw",
      ),
    ).toBeInTheDocument();
  });

  it("shows the label alone when nothing was held back", () => {
    renderVerdict(byId("fix-ready-no-held", { limited_validation: true }));
    expect(screen.getByText("Limited validation")).toBeInTheDocument();
    expect(screen.queryByText("Proven on the targets only")).toBeNull();
    expect(screen.queryByText(/Too few to tell/)).toBeNull();
  });

  it("is named in the list's Result column", () => {
    expect(
      listResult(byId("fix-ready-archive", { limited_validation: true })).note,
    ).toMatch(/^Limited validation · /);
  });
});

describe("a fix proven on enough cases", () => {
  it("keeps its wording", () => {
    renderVerdict(byId("fix-ready-no-held"));
    expect(screen.queryByText("Limited validation")).toBeNull();
    expect(screen.getByText("Proven on the targets only")).toBeInTheDocument();
  });
});
