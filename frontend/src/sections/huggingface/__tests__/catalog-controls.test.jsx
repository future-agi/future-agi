import { createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "src/utils/test-utils";
import { endpoints } from "src/utils/axios";
import HuggingFaceView from "../HuggingFaceView";

const mode = vi.hoisted(() => ({ prototype: false }));
const http = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => mode.prototype,
}));
vi.mock("src/launch/launchSwitch", async (importOriginal) => ({
  ...(await importOriginal()),
  isSurfaceShown: () => true,
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const real = /** @type {typeof import("src/utils/axios")} */ (
    await importOriginal()
  );
  return { ...real, default: { ...real.default, post: http.post } };
});
vi.mock("src/utils/Mixpanel", async (importOriginal) => ({
  ...(await importOriginal()),
  trackEvent: () => {},
}));

beforeEach(() => {
  http.post.mockReset().mockResolvedValue({
    data: { result: { datasets: [], total_datasets: 0 } },
  });
});

describe("Hugging Face catalog controls", () => {
  it.each([false, true])(
    "has no curated ordering control or request field (prototype=%s)",
    async (prototype) => {
      mode.prototype = prototype;
      const client = new QueryClient({
        defaultOptions: { queries: { retry: false, gcTime: 0 } },
      });
      const view = render(
        createElement(
          QueryClientProvider,
          { client },
          createElement(HuggingFaceView),
        ),
      );
      try {
        await waitFor(() =>
          expect(http.post).toHaveBeenCalledWith(
            endpoints.huggingFace.list,
            expect.objectContaining({ search_query: "" }),
            expect.any(Object),
          ),
        );
        expect(
          screen.queryByRole("checkbox", { name: "Curated benchmarks first" }),
        ).not.toBeInTheDocument();
        expect(
          screen.queryByTestId("hf-curated-first"),
        ).not.toBeInTheDocument();
        const calls = http.post.mock.calls.filter(
          ([url]) => url === endpoints.huggingFace.list,
        );
        expect(calls.length).toBeGreaterThan(0);
        for (const [, body] of calls) {
          expect(body.filter_params).not.toHaveProperty("curated");
        }
        expect(screen.getByPlaceholderText("Search")).toBeInTheDocument();
        expect(screen.getByText("Sort: Trending")).toBeInTheDocument();
      } finally {
        view.unmount();
        client.clear();
      }
    },
  );
});
