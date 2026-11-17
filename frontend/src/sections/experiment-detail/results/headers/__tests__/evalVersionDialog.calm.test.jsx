// See changes for an eval column scored with an older version, where this
// build has no route for the version preview: the amber label and See
// changes stay and the dialog opens; the shared line stands once where the
// preview would be, with no grey placeholder and no per-run error; no
// preview is asked for; a run's box draws no "Use v3 on #1A", so nothing
// there asks for a re-score; and "Use v3 in new runs" and Close stay and
// work. Where the route is offered, it works as before: "Use v3 on #1A"
// comes with the run's preview.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { endpoints } from "src/utils/axios";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import ResultGroupHeader from "../ResultGroupHeader";
import { useEvalVersionDoor } from "../useEvalVersionDoor";

const PREVIEW =
  "/model-hub/eval-templates/{template_id}/versions/{version}/preview/";

const h = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  absent: null,
  requestRescore: null,
}));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { ...actual.default, get: h.get, post: h.post },
  };
});
// A case names the routes this build lacks; otherwise the real list.
vi.mock("src/launch/absentPathGuard", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    absentTemplateOf: (url, ...rest) =>
      h.absent
        ? h.absent.includes(url)
          ? url
          : null
        : actual.absentTemplateOf(url, ...rest),
  };
});
vi.mock("notistack", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
// The tooltip's text is drawn next to what it wraps, so a test can read it.
vi.mock("src/components/tooltip", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ title, children }) =>
      createElement(
        "span",
        null,
        children,
        createElement("span", { "data-testid": "tip" }, title),
      ),
  };
});

// One run in view, #1A, scored with v2; the template's newest is v3.
const DEF = {
  key: "eval:accuracy",
  name: "Accuracy",
  template_id: "t1",
  versions: { r1: "v2" },
  latest_version: "v3",
  uem_ids: { r1: "uem-1" },
};
const RUNS = { r1: { label: "#1A", experiment_id: "exp-1" } };
const groupWith = (onSeeChanges) => ({
  key: DEF.key,
  kind: "eval",
  name: "Accuracy",
  def: DEF,
  versions: DEF.versions,
  latestVersion: "v3",
  runLabels: { r1: "#1A" },
  onSeeChanges,
  menuItems: [],
});
const previewCalls = () =>
  h.get.mock.calls.filter(([url]) => String(url).includes("/preview/"));
const rerunCalls = () =>
  h.post.mock.calls.filter(([url]) => String(url).includes("/rerun-cells/"));
// Presses every control a run's box draws.
const pressRunBoxes = (dialog) =>
  within(dialog)
    .getAllByTestId("eval-version-preview")
    .flatMap((box) => within(box).queryAllByRole("button"))
    .forEach((button) => fireEvent.click(button));

function DoorWithDialog() {
  const door = useEvalVersionDoor({
    runsById: RUNS,
    requestRescore: h.requestRescore,
    patch: vi.fn(),
    datasetId: "d1",
  });
  return (
    <>
      <ResultGroupHeader
        group={groupWith(() => door.seeChanges(groupWith()))}
      />
      {door.dialog}
    </>
  );
}
let client;
const mount = () =>
  render(
    <QueryClientProvider client={client}>
      <DoorWithDialog />
    </QueryClientProvider>,
  );
const openSeeChanges = async () => {
  fireEvent.click(screen.getByTestId("eval-version-drift"));
  await screen.findByTestId("eval-version-changes");
  // The whole dialog: its actions ("Use v3 in new runs", Close) sit below
  // the content block that carries the test id.
  return screen.getByRole("dialog");
};

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  h.get.mockReset().mockReturnValue(new Promise(() => {}));
  h.post.mockReset().mockResolvedValue({ data: { result: {} } });
  h.requestRescore = vi.fn();
  h.absent = null;
});
afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
});

describe("See changes where the version preview has no route", () => {
  beforeEach(() => {
    setLaunchOverrideForTest({ launch: true });
    h.absent = [PREVIEW];
  });

  it("keeps the amber label and See changes", () => {
    // Green at the base: the label, its tooltip and its door are unchanged.
    const onSeeChanges = vi.fn();
    render(<ResultGroupHeader group={groupWith(onSeeChanges)} />);
    expect(screen.getByTestId("eval-version-drift")).toHaveTextContent(
      "v2 · v3 available",
    );
    expect(screen.getAllByTestId("tip").map((t) => t.textContent)).toContain(
      "Accuracy v3 available · See changes",
    );
    fireEvent.click(screen.getByTestId("eval-version-drift"));
    expect(onSeeChanges).toHaveBeenCalledTimes(1);
  });

  it("opens the dialog with the shared line once", async () => {
    mount();
    const dialog = await openSeeChanges();
    expect(within(dialog).getAllByText(NOT_IN_RELEASE)).toHaveLength(1);
    expect(dialog.querySelector(".MuiSkeleton-root")).toBeNull();
    expect(
      within(dialog).queryByText("Could not compute what would change"),
    ).toBeNull();
    expect(within(dialog).getByText("#1A · scored with v2")).toBeTruthy();
  });

  it("asks for no preview", async () => {
    mount();
    await openSeeChanges();
    await Promise.resolve();
    expect(previewCalls()).toEqual([]);
  });

  it("draws no Use v3 on the run", async () => {
    mount();
    const dialog = await openSeeChanges();
    expect(within(dialog).getByText("#1A · scored with v2")).toBeTruthy();
    expect(
      within(dialog).queryByRole("button", { name: /^Use v3 on/ }),
    ).toBeNull();
  });

  it("asks the re-score door for nothing from a run's box", async () => {
    mount();
    pressRunBoxes(await openSeeChanges());
    expect(h.requestRescore).not.toHaveBeenCalled();
  });

  it("sends no re-score request from a run's box", async () => {
    // The door stands in as a plain sender of the requests it is handed.
    h.requestRescore = vi.fn((r) =>
      r.posts.forEach((p) => h.post(p.url, p.body)),
    );
    mount();
    pressRunBoxes(await openSeeChanges());
    expect(rerunCalls()).toEqual([]);
  });

  it("keeps Use v3 in new runs, and it pins the version", async () => {
    // Green at the base: the footer action is served and stays.
    mount();
    const dialog = await openSeeChanges();
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Use v3 in new runs" }),
    );
    await waitFor(() => expect(h.post).toHaveBeenCalledTimes(1));
    expect(h.post.mock.calls[0][0]).toBe(
      endpoints.develop.eval.editEval("d1", "uem-1"),
    );
    expect(h.post.mock.calls[0][1]).toEqual({
      experiment_id: "exp-1",
      run: false,
      config: { version_pin: "v3" },
    });
    expect(rerunCalls()).toEqual([]);
    expect(previewCalls()).toEqual([]);
  });

  it("keeps Close, and it closes the dialog", async () => {
    // Green at the base.
    mount();
    const dialog = await openSeeChanges();
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() =>
      expect(screen.queryByTestId("eval-version-changes")).toBeNull(),
    );
  });
});

describe("See changes where the version preview is offered", () => {
  it("asks for the preview of the older run, with no shared line", async () => {
    // Green at the base.
    setLaunchOverrideForTest({ launch: true });
    h.absent = [];
    mount();
    const dialog = await openSeeChanges();
    await waitFor(() => expect(previewCalls()).toHaveLength(1));
    expect(previewCalls()[0][1]).toEqual({ params: { run_id: "r1" } });
    expect(within(dialog).queryByText(NOT_IN_RELEASE)).toBeNull();
  });

  it("draws Use v3 on the run with its preview, and it asks to re-score it with v3", async () => {
    setLaunchOverrideForTest({ launch: true });
    h.absent = [];
    h.get.mockResolvedValue({
      data: {
        result: {
          run_version: "v2",
          run_label: "#1A",
          changes: [],
          would_flip: [],
        },
      },
    });
    mount();
    const dialog = await openSeeChanges();
    fireEvent.click(
      await within(dialog).findByRole("button", { name: "Use v3 on #1A" }),
    );
    expect(h.requestRescore).toHaveBeenCalledTimes(1);
    expect(h.requestRescore.mock.calls[0][0].posts[0].body).toEqual({
      user_eval_metric_ids: ["uem-1"],
      run_ids: ["r1"],
      eval_versions: { "eval:accuracy": "v3" },
    });
  });
});
