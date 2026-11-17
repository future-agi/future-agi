// This build ships the tags route: Edit experiment offers its Tags field
// without an unavailable sentence on hover or keyboard focus. The picker
// opens, and a rename saves with a tag list read but no experiment tags write.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "src/utils/test-utils";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import RenameExperimentDialog from "../RenameExperimentDialog";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: api,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/api/develop/experiment-create", async (importOriginal) => ({
  ...(await importOriginal()),
  useExperimentNameCheck: () => ({ data: true }),
}));
vi.mock("src/hooks/use-debounce", async (importOriginal) => ({
  ...(await importOriginal()),
  useDebounce: (value) => value,
}));

const SENTENCE = "Tags are not part of this release.";
const TARGET = { id: "exp-1", name: "Prompt v1", description: "First try" };

const tagRequests = () =>
  [...api.get.mock.calls, ...api.post.mock.calls, ...api.put.mock.calls]
    .map(([url]) => String(url))
    .filter((url) => url.includes("tags"));

function open() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const onSave = vi.fn(() => Promise.resolve({}));
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <RenameExperimentDialog
        target={TARGET}
        datasetId="ds-1"
        onClose={onClose}
        onSave={onSave}
      />
    </QueryClientProvider>,
  );
  return {
    onSave,
    onClose,
    tagsButton: screen.getByTestId("experiment-tags-edit"),
  };
}

beforeEach(() => {
  setLaunchOverrideForTest({ showUnreleased: false, launch: true });
  api.get.mockResolvedValue({ data: { result: { results: [] } } });
  api.put.mockResolvedValue({ data: { result: {} } });
});

afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
  vi.clearAllMocks();
});

describe("Edit experiment where this build ships the tags route", () => {
  it("shows the Tags button enabled without its old sentence on hover", () => {
    const { tagsButton } = open();
    expect(tagsButton).toBeEnabled();
    expect(tagsButton).not.toHaveAttribute("aria-disabled");
    fireEvent.mouseOver(tagsButton);
    expect(screen.queryByText(SENTENCE)).not.toBeInTheDocument();
  });

  it("focuses the Tags button without an unavailable description", () => {
    const { tagsButton } = open();
    tagsButton.focus();
    expect(tagsButton).toHaveFocus();
    expect(tagsButton).not.toHaveAccessibleDescription();
  });

  it("opens the tag picker", () => {
    const { tagsButton } = open();
    fireEvent.click(tagsButton);
    expect(screen.getByText("Tags on this experiment")).toBeVisible();
    expect(screen.queryByText(/Create tag/)).not.toBeInTheDocument();
  });

  it("saves a rename with a tags read but no tags write", async () => {
    const { onSave } = open();
    fireEvent.change(screen.getByRole("textbox", { name: /^Name/ }), {
      target: { value: "Prompt v2" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(onSave).toHaveBeenCalledWith({ id: "exp-1", name: "Prompt v2" });
    expect(tagRequests()).toEqual(["/model-hub/tags/"]);
  });
});
