// Where this build has no tags route, the selection bar's More menu keeps
// Tag…, disabled, with one sentence a person can read on hover and on
// keyboard focus. It opens no picker (no Create tag, no Manage tags) and no
// tags request is sent, not even the tag list read on load.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "src/utils/test-utils";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import RunSelectionMore from "../RunSelectionMore";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: api,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
// The COLLAB claim backs the tag routes in every launch build (EXP-E2E-010),
// so these tests name them absent themselves, as a build whose absent list
// still has them.
const absent = vi.hoisted(() => ({
  routes: [
    "/model-hub/tags/",
    "/model-hub/experiments/v2/{experiment_id}/tags/",
  ],
}));
vi.mock("src/launch/absentPathGuard", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    absentTemplateOf: (url, ...rest) =>
      absent.routes.includes(url) ? url : actual.absentTemplateOf(url, ...rest),
  };
});

const SENTENCE = "Tags are not part of this release.";
const RUNS = [{ run_id: "run-1", experiment_id: "exp-1", label: "#1A" }];

const tagRequests = () =>
  [...api.get.mock.calls, ...api.post.mock.calls, ...api.put.mock.calls]
    .map(([url]) => String(url))
    .filter((url) => url.includes("tags"));

function openMenu() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const onFilterToSelected = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <RunSelectionMore runs={RUNS} onFilterToSelected={onFilterToSelected} />
    </QueryClientProvider>,
  );
  fireEvent.click(
    screen.getByRole("button", { name: "More actions on the selected runs" }),
  );
  return {
    tag: screen.getByRole("menuitem", { name: /^Tag…/ }),
    filter: screen.getByRole("menuitem", { name: "Filter to selected" }),
    onFilterToSelected,
  };
}

beforeEach(() => {
  setLaunchOverrideForTest({ launch: true });
  api.get.mockResolvedValue({ data: { result: { results: [] } } });
});

afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
  vi.clearAllMocks();
});

describe("Tag… where this build has no tags route", () => {
  it("is shown disabled with its sentence on hover", () => {
    const { tag } = openMenu();
    expect(tag).toHaveAttribute("aria-disabled", "true");
    fireEvent.mouseOver(tag);
    expect(within(tag).getByText(SENTENCE)).toBeVisible();
  });

  it("is reached by keyboard and names its reason on focus", () => {
    const { tag, filter } = openMenu();
    filter.focus();
    fireEvent.keyDown(filter, { key: "ArrowUp" });
    expect(tag).toHaveFocus();
    expect(tag).toHaveAccessibleDescription(SENTENCE);
  });

  it("opens no tag picker and sends no tags request", () => {
    const { tag } = openMenu();
    fireEvent.click(tag);
    expect(screen.queryByText(/Tags on 1 experiment/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Create tag/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Manage tags/)).not.toBeInTheDocument();
    expect(tagRequests()).toEqual([]);
  });

  it("keeps Filter to selected working", () => {
    const { filter, onFilterToSelected } = openMenu();
    fireEvent.click(filter);
    expect(onFilterToSelected).toHaveBeenCalledWith(["run-1"]);
  });
});
