// Translation is listed in the launch registry but a deployment does not
// offer it: its route is absent. The grid's Translate cell… and a Row view
// field's Translate to… stay, disabled, each tied to the shared line; the
// translate read is never sent and a translate is refused with that line
// without a request. Where the registry makes translation real, both work.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import axios from "src/utils/axios";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import { setCollabOfferForTest } from "../collabOffer";
import { buildCellContextMenu } from "src/sections/develop-detail/DataTab/DataGrid/gridContextMenu";
import { useTranslate, useTranslateInfo } from "src/api/develop/row-collab";

const notified = vi.hoisted(() => []);
vi.mock("src/sections/common/ux/toast", () => ({
  notify: (...args) => notified.push(args),
  default: (...args) => notified.push(args),
}));

const { default: FieldCollabMenu } = await import("../FieldCollabMenu");

const params = () => ({
  column: {
    getColDef: () => ({
      col: { id: "c1" },
      headerName: "customer_message",
      dataType: "text",
    }),
    getColId: () => "c1",
  },
  node: { data: { row_id: "r1", row_meta: { split_ids: [] } } },
  value: "注文がまだ届いていません",
});
const handlers = () => ({
  filterTo: vi.fn(),
  exclude: vi.fn(),
  showAllIn: vi.fn(),
  openRow: vi.fn(),
  copyValue: vi.fn(),
  copyRowLink: vi.fn(),
  addToQueue: vi.fn(),
  toggleSplit: vi.fn(),
  manageSplits: vi.fn(),
  commentOnRow: vi.fn(),
  assignRow: vi.fn(),
  translateCell: vi.fn(),
});
const translateItem = (h) =>
  buildCellContextMenu(params(), {
    handlers: h,
    collab: { canAssign: true },
  }).find((i) => i?.name === "Translate cell…");

const client = () =>
  new QueryClient({ defaultOptions: { queries: { retry: false } } });
const wrapper = ({ children }) => (
  <QueryClientProvider client={client()}>{children}</QueryClientProvider>
);
const mountField = (props = {}, url = "/dashboard/develop/d1?row=r1") =>
  render(
    <QueryClientProvider client={client()}>
      <MemoryRouter initialEntries={[url]}>
        <FieldCollabMenu
          datasetId="d1"
          rowId="r1"
          columnId="c1"
          name="customer_message"
          rowLabel="Row 1"
          onTranslated={() => {}}
          {...props}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
const translateCalls = (spy) =>
  spy.mock.calls.filter(([url]) => String(url).includes("/translate/"));
// Let any request the page would send leave first.
const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

let get;
let post;
beforeEach(() => {
  setCollabOfferForTest({ all: false });
  get = vi.spyOn(axios, "get").mockResolvedValue({
    data: {
      result: {
        languages: [
          { code: "ja", name: "Japanese" },
          { code: "en", name: "English" },
        ],
        fields: [
          {
            column_id: "c1",
            name: "customer_message",
            language: "ja",
            language_name: "Japanese",
            available: ["en"],
          },
        ],
      },
    },
  });
  post = vi.spyOn(axios, "post").mockResolvedValue({
    data: { result: { target: "en", translations: [] } },
  });
});
afterEach(() => {
  cleanup();
  setCollabOfferForTest(null);
  vi.restoreAllMocks();
  notified.length = 0;
});

describe("the grid's Translate cell… where translation is not offered", () => {
  it("stays in the cell menu, disabled, with the shared line as its tooltip", () => {
    const item = translateItem(handlers());
    expect(item).toBeTruthy();
    expect(item.disabled).toBe(true);
    expect(item.tooltip).toBe(NOT_IN_RELEASE);
  });

  it("does nothing when pressed", () => {
    const h = handlers();
    translateItem(h).action?.();
    expect(h.translateCell).not.toHaveBeenCalled();
  });
});

describe("a Row view field's Translate to… where translation is not offered", () => {
  it("stays in the field's ⋯ menu, disabled and described by the shared line", async () => {
    mountField({ canComment: false });
    await settle();
    fireEvent.click(
      screen.getByRole("button", { name: "More actions for customer_message" }),
    );
    const item = screen.getByRole("menuitem", { name: "Translate to…" });
    expect(item).toHaveAttribute("aria-disabled", "true");
    expect(item).toHaveAccessibleDescription(NOT_IN_RELEASE);
    expect(item.textContent).toContain(NOT_IN_RELEASE);
  });

  it("sends no translate read", async () => {
    mountField();
    await settle();
    expect(translateCalls(get)).toEqual([]);
  });

  it("the grid's deep link says the shared line and opens no languages", async () => {
    mountField(
      { autoOpen: true },
      "/dashboard/develop/d1?row=r1&rtranslate=c1",
    );
    await settle();
    expect(notified).toContainEqual([NOT_IN_RELEASE, { variant: "info" }]);
    expect(screen.queryByTestId("field-translate-to-en")).toBeNull();
    expect(translateCalls(get)).toEqual([]);
    expect(translateCalls(post)).toEqual([]);
  });

  it("keeps Comment on this field beside it", async () => {
    mountField({ canComment: true });
    await settle();
    fireEvent.click(
      screen.getByRole("button", { name: "More actions for customer_message" }),
    );
    expect(
      screen.getByRole("menuitem", { name: "Comment on this field" }),
    ).not.toHaveAttribute("aria-disabled");
  });
});

describe("the translate hooks where translation is not offered", () => {
  it("the read is never sent, whatever the caller asks", async () => {
    renderHook(() => useTranslateInfo("d1", "r1"), { wrapper });
    renderHook(() => useTranslateInfo("d1", "r1", { enabled: true }), {
      wrapper,
    });
    renderHook(() => useTranslateInfo("d1", undefined, { enabled: true }), {
      wrapper,
    });
    await settle();
    expect(translateCalls(get)).toEqual([]);
  });

  it("a translate is refused with the shared line, without a request", async () => {
    const { result } = renderHook(() => useTranslate("d1"), { wrapper });
    let error;
    await act(async () => {
      error = await result.current
        .mutateAsync({ row_id: "r1", column_ids: ["c1"], target: "en" })
        .then(
          () => null,
          (e) => e,
        );
    });
    expect(error).toBeInstanceOf(Error);
    expect(error.message).toBe(NOT_IN_RELEASE);
    expect(translateCalls(post)).toEqual([]);
  });
});

describe("where the registry makes translation real", () => {
  beforeEach(() =>
    setCollabOfferForTest({
      all: false,
      madeReal: ["dataset.collab.translate"],
    }),
  );

  it("Translate cell… is enabled and opens the field's Translate to…", () => {
    const h = handlers();
    const item = translateItem(h);
    expect(Boolean(item.disabled)).toBe(false);
    item.action();
    expect(h.translateCell).toHaveBeenCalledWith("r1", "c1");
  });

  it("the field reads its languages and offers them", async () => {
    mountField({ canComment: false });
    // The ⋯ shows once the read names a language to translate to.
    fireEvent.click(
      await screen.findByRole("button", {
        name: "More actions for customer_message",
      }),
    );
    expect(translateCalls(get)).toHaveLength(1);
    const item = screen.getByRole("menuitem", { name: "Translate to…" });
    expect(item).not.toHaveAttribute("aria-disabled");
  });
});
