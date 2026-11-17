import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, renderHook, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { render } from "src/utils/test-utils";
const net = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get: net.get },
  endpoints: { develop: { getDatasetDetail: (id) => `/datasets/${id}` } },
}));
import { DatasetReadError, useDatasetAvailability } from "../DatasetNotFound";

const wrapper = ({ children }) => (
  <QueryClientProvider
    client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
  >
    {children}
  </QueryClientProvider>
);

describe("dataset availability gates", () => {
  it("holds the grid while an unlisted dataset's scoped read is unresolved", async () => {
    let finish;
    net.get.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const { result } = renderHook(
      () => useDatasetAvailability("unknown", { list: [], listLoading: false }),
      { wrapper },
    );
    expect(result.current.pending).toBe(true);
    finish({ data: { result: {} } });
    await waitFor(() => expect(result.current.pending).toBe(false));
    expect(result.current.missing).toBe(false);
  });
  it("uses the same neutral recovery for denied and missing datasets", () => {
    render(
      <HelmetProvider>
        <DatasetReadError error={{ statusCode: 403 }} />
      </HelmetProvider>,
    );
    expect(screen.getByText("Dataset not found")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Back to Datasets" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/foreign|another organization/i),
    ).not.toBeInTheDocument();
  });
  it("offers explicit retry after a server read failure", () => {
    const retry = vi.fn();
    render(<DatasetReadError error={{ statusCode: 500 }} onRetry={retry} />);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Could not load dataset",
    );
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalledOnce();
  });
});
