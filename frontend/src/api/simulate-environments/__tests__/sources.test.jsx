import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import PropTypes from "prop-types";
import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { buildSourceReview } from "src/sections/simulate/environments/workspace/sources/sourceReview";
import { usageOf } from "src/sections/simulate/environments/workspace/sources/sources.constants";
import axios from "src/utils/axios";
import { editColumn } from "src/sections/simulate/environments/workspace/sources/mapping";
import { draftReviewKey } from "../draftReview";
import {
  sourceSummaryKey,
  useAcceptSource,
  usePrepareKbSource,
  usePrepareUploadSource,
  useRevertFileEdits,
  useUpdateSource,
} from "../sources";
import { kbChangesOf, kbRefreshed, readSource } from "../_fixtures/sources";

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { post: vi.fn() },
}));

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

const accept = async (prepared) => {
  const { result } = renderHook(() => useAcceptSource("env-1"), { wrapper });
  await act(() =>
    result.current.mutateAsync({
      import_ref: prepared.import_ref,
      expected_environment_version_id:
        prepared.capabilities.environment_version_id,
      expected_draft: prepared.capabilities.draft,
      expected_draft_digest: prepared.draft_digest,
      review: buildSourceReview(prepared.draft),
      ui: {
        accepted: prepared.ui.items.filter((item) => !item.blocking),
        failed: [],
      },
    }),
  );
};

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
      fileOf("Refund policy 2026.pdf"),
    ]);

    // Refused by its extension at once, so it never shows as reading.
    expect(ui.sources.map((source) => source.status)).toEqual([
      "unsupported",
      "reading",
      "reading",
    ]);
    expect(ui.sources.map((source) => source.ui.settlesTo?.status)).toEqual([
      undefined,
      "unreadable",
      "not_saved",
    ]);
    expect(ui.items.map((item) => item.title)).toContain(
      "Rule: Refunds are allowed within 30 days of delivery.",
    );
  });

  it("accepting moves the files read to ready, used by its rules and scenarios", async () => {
    const prepared = await prepare([fileOf("refund-policy.pdf")]);
    await act(async () => vi.advanceTimersByTime(5000));
    expect(statusesOf()).toEqual(["not_saved"]);

    await accept(prepared);
    expect(statusesOf()).toEqual(["ready"]);
    // 3 rules and the two day-boundary scenarios citing it, as the prototype counts.
    const [saved] = client.getQueryData(sourceSummaryKey("env-1")).sources;
    expect(usageOf(saved).count).toBe(5);
  });

  it("a mapping edit on a saved file is one draft row until it matches the saved file again", async () => {
    await accept(await prepare([fileOf("orders.csv")]));
    const { result } = renderHook(
      () => ({
        update: useUpdateSource("env-1"),
        revert: useRevertFileEdits("env-1"),
      }),
      { wrapper },
    );
    const [{ source_id: id }] = client.getQueryData(
      sourceSummaryKey("env-1"),
    ).sources;
    const pointAt = (ref) =>
      act(() =>
        result.current.update(id, (one) =>
          editColumn(one, "customer_id", { ref }),
        ),
      );
    const row = () =>
      client
        .getQueryData(draftReviewKey("env-1"))
        .changes.find((one) => one.id === `world_state:mapping-${id}`);
    const refNow = () =>
      client
        .getQueryData(sourceSummaryKey("env-1"))
        .sources[0].ui.parsed.columns.find((one) => one.name === "customer_id")
        .ref;

    pointAt("users.id");
    expect(row().summary).toBe("orders.csv: customer_id points at users.id");
    pointAt("customers.customer_id");
    expect(row()).toBeUndefined();

    pointAt("users.id");
    act(() => result.current.revert([row()]));
    expect(row()).toBeUndefined();
    expect(refNow()).toBe("customers.customer_id");
  });

  it("a Knowledge base copy changes from what it read, and a refresh takes the changes", () => {
    const origin = { kind: "knowledge_base", kb_id: "kb", file_id: "any-id" };
    const policy = readSource({ name: "Refund policy.pdf", size: 1, origin });
    const [changed, added] = kbChangesOf(policy);
    expect(changed.from).toBe(policy.ui.parsed.passages[0].text);
    expect(changed.to).toBe(
      "Items can be returned within 10 days of delivery for a full refund.",
    );
    expect(added.kind).toBe("rule-added");

    const table = readSource({ name: "orders.csv", size: 1, origin });
    const changes = kbChangesOf(table);
    expect(changes.map((change) => change.label)).toEqual([
      "40 rows",
      "3 rows",
      "2 rows",
    ]);
    expect(kbRefreshed(table, changes).ui.parsed.rows).toBe(538);
  });

  it("Add all copies the ready files from every page of the Knowledge base", async () => {
    const fileRow = (n, status = "Completed") => ({
      id: `f-${n}`,
      name: `doc-${n}.md`,
      file_size: n,
      status,
    });
    const pages = [
      [
        ...Array.from({ length: 99 }, (_, n) => fileRow(n)),
        fileRow(99, "Failed"),
      ],
      [fileRow(100), fileRow(101, "Processing")],
    ];
    axios.post.mockImplementation(async (_, body) => ({
      data: {
        result: { table_data: pages[body.page_number], total_rows: 102 },
      },
    }));
    const { result } = renderHook(() => usePrepareKbSource("env-1"), {
      wrapper,
    });
    let response;
    await act(async () => {
      response = await result.current.mutateAsync({
        selection: {
          kind: "knowledge_base",
          kb: { id: "00000000-0000-4000-8000-000000000001", name: "Docs" },
          whole_collection: true,
        },
      });
    });

    expect(axios.post).toHaveBeenCalledTimes(2);
    expect(response.ui.sources).toHaveLength(100);
    expect(response.ui.sources.at(-1).origin).toMatchObject({
      kb_id: "00000000-0000-4000-8000-000000000001",
      file_id: "f-100",
      collection: "Docs",
    });
  });
});
