import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "src/utils/test-utils";

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get: vi.fn() },
}));
const axios = (await import("src/utils/axios")).default;
const { endpoints } = await import("src/utils/axios");
const { default: OpenInEvalsLink } = await import("../OpenInEvalsLink");

const VERSIONS = {
  versions: [{ version_number: 3, is_default: true }, { version_number: 2 }],
};
const SAVED = {
  evals: [
    { id: "uem-1", template_id: "t-from-list", metric_key: "eval:correctness" },
  ],
};

const renderLink = (props) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <OpenInEvalsLink {...props} />
    </QueryClientProvider>,
  );

describe("OpenInEvalsLink", () => {
  beforeEach(() => {
    axios.get.mockReset();
    axios.get.mockImplementation(async (url) => {
      if (url === endpoints.develop.eval.getEvalsList("ds-1"))
        return { data: { result: SAVED } };
      if (url.includes("/versions/")) return { data: { result: VERSIONS } };
      throw new Error(`unexpected ${url}`);
    });
  });

  it("opens the def's template at the run's version", async () => {
    renderLink({
      def: { key: "eval:correctness", template_id: "t-1", name: "Correctness" },
      version: "v2",
    });
    const link = await screen.findByTestId("open-in-evals-text");
    expect(link.getAttribute("href")).toBe("/dashboard/evaluations/t-1?v=2");
    expect(link.getAttribute("aria-label")).toBe(
      "Open Correctness v2 in Evals",
    );
    expect(link.textContent).toBe("Open in Evals ↗");
    // def.template_id wins: the saved-evals list is not read.
    expect(
      axios.get.mock.calls.some(
        ([u]) => u === endpoints.develop.eval.getEvalsList("ds-1"),
      ),
    ).toBe(false);
  });

  it("falls back to the dataset's saved evals, and drops a version the template no longer lists", async () => {
    renderLink({
      datasetId: "ds-1",
      def: { key: "eval:correctness", name: "Correctness" },
      version: "v1",
    });
    const link = await screen.findByTestId("open-in-evals-text");
    await waitFor(() =>
      expect(link.getAttribute("href")).toBe(
        "/dashboard/evaluations/t-from-list",
      ),
    );
    expect(link.getAttribute("aria-label")).toBe(
      "This run used v1, which Correctness no longer lists. Opens the current version (v3).",
    );
    expect(link.getAttribute("data-version-missing")).toBe("true");
  });

  it("renders no link without a template; the chip stays as it was", async () => {
    renderLink({
      datasetId: "ds-1",
      def: { key: "eval:other", name: "Other" },
      version: "v1",
      variant: "chip",
      children: <span>v1</span>,
    });
    await waitFor(() => expect(axios.get).toHaveBeenCalled());
    expect(screen.queryByTestId("open-in-evals-chip")).toBeNull();
    expect(screen.getByText("v1")).toBeTruthy();
  });
});
