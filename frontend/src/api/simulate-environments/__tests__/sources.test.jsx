import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import PropTypes from "prop-types";
import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { buildSourceReview } from "src/sections/simulate/environments/workspace/sources/sourceReview";
import {
  sourceSummaryKey,
  useAcceptSource,
  usePrepareUploadSource,
} from "../sources";

let client;
const wrapper = ({ children }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);
wrapper.propTypes = { children: PropTypes.node };

const fileOf = (name, body = "x") => new File([body], name);
const statusesOf = () =>
  client
    .getQueryData(sourceSummaryKey("env-1"))
    .sources.map((source) => source.status);

const prepare = async (files) => {
  const { result } = renderHook(() => usePrepareUploadSource("env-1"), {
    wrapper,
  });
  let response;
  await act(async () => {
    response = await result.current.mutateAsync({
      files,
      versionId: "version-1",
      draft: { id: null, revision: 0 },
    });
  });
  return response;
};

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});
afterEach(() => vi.useRealTimers());

describe("environment sources stub", () => {
  it("adds one source per file and skips a file picked again", async () => {
    const first = await prepare([fileOf("orders.csv"), fileOf("orders.csv")]);
    const again = await prepare([fileOf("orders.csv"), fileOf("policy.pdf")]);

    expect(first.ui.sources).toHaveLength(1);
    expect(again.ui.sources.map((source) => source.name)).toEqual([
      "policy.pdf",
    ]);
    expect(statusesOf()).toEqual(["reading", "reading"]);
  });

  it("settles unsupported and scanned files without blocking the rest", async () => {
    const { ui } = await prepare([
      fileOf("notes.pages"),
      fileOf("policy-scan.pdf"),
      fileOf("policy.pdf"),
    ]);

    expect(ui.sources.map((source) => source.ui.settlesTo.status)).toEqual([
      "unsupported",
      "unreadable",
      "not_saved",
    ]);
    expect(ui.items.some((item) => item.kind === "rule")).toBe(true);
  });

  it("accepting moves the files read from not saved to ready", async () => {
    const prepared = await prepare([fileOf("policy.pdf")]);
    await act(async () => vi.advanceTimersByTime(5000));
    expect(statusesOf()).toEqual(["not_saved"]);

    const { result } = renderHook(() => useAcceptSource("env-1"), { wrapper });
    await act(() =>
      result.current.mutateAsync({
        import_ref: prepared.import_ref,
        expected_environment_version_id:
          prepared.capabilities.environment_version_id,
        expected_draft: prepared.capabilities.draft,
        expected_draft_digest: prepared.draft_digest,
        review: buildSourceReview(prepared.draft),
      }),
    );
    expect(statusesOf()).toEqual(["ready"]);
  });
});
