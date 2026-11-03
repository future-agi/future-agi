import React, { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { enqueueSnackbar } from "notistack";

import ThemeProvider from "src/theme";
import { palette } from "src/theme/palette";
import axios, { endpoints } from "src/utils/axios";
import SaveAndCommit from "../SaveAndCommit";

const settings = vi.hoisted(() => ({
  themeMode: "light",
  themeDirection: "ltr",
  themeContrast: "default",
  themeColorPresets: "default",
}));

vi.mock("src/components/settings", () => ({
  useSettingsContext: () => settings,
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const original = await importOriginal();
  return { ...original, default: { post: vi.fn() } };
});

const COMMIT_URL = endpoints.develop.runPrompt.commitSavePrompt("prompt-123");
const DEFAULT_ACTION = "Commit and set as a default version";
const getCommitButton = () =>
  screen.getByRole("button", { name: "Commit", exact: true });
const getDefaultButton = () =>
  screen.getByRole("button", { name: DEFAULT_ACTION });
const getMessage = () =>
  screen.getByPlaceholderText("Enter a commit message for this version...");

function deferredSave() {
  let resolve;
  let reject;
  const promise = new Promise((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return { promise, resolve, reject };
}

function renderDialog(isDefault = false) {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const onClose = vi.fn();

  function DialogHarness() {
    const [open, setOpen] = useState(true);
    return (
      <>
        <button type="button" onClick={() => setOpen(true)}>
          Reopen commit dialog
        </button>
        <SaveAndCommit
          open={open}
          onClose={() => {
            onClose();
            setOpen(false);
          }}
          data={{ version: "v1", isDefault, isDraft: false }}
          promptName="Synthetic prompt"
        />
      </>
    );
  }

  render(
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <MemoryRouter initialEntries={["/prompts/prompt-123"]}>
          <Routes>
            <Route path="/prompts/:id" element={<DialogHarness />} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
  return { onClose, invalidate };
}

// Render the production overrides: the lightweight test-utils theme omits them.
describe.each(["light", "dark"])("SaveAndCommit in %s mode", (mode) => {
  beforeEach(() => {
    vi.clearAllMocks();
    settings.themeMode = mode;
    axios.post.mockReset();
  });

  it("distinguishes enabled and disabled appearance with the production theme", async () => {
    const user = userEvent.setup();
    renderDialog();
    const commit = getCommitButton();
    expect(commit).toBeDisabled();
    expect(getDefaultButton()).toBeDisabled();
    expect(commit).toHaveStyle({ color: palette(mode).text.disabled });
    const disabledColor = getComputedStyle(commit).color;

    await user.type(getMessage(), "Clarify the support reply");
    expect(commit).toBeEnabled();
    expect(getDefaultButton()).toBeEnabled();
    expect(commit).toHaveStyle({ color: palette(mode).text.primary });
    expect(getComputedStyle(commit).color).not.toBe(disabledColor);

    await user.clear(getMessage());
    expect(commit).toBeDisabled();
    expect(getDefaultButton()).toBeDisabled();
    expect(commit).toHaveStyle({ color: palette(mode).text.disabled });
    expect(axios.post).not.toHaveBeenCalled();
  });

  it.each(["Commit", DEFAULT_ACTION])(
    "disables both actions while %s is pending, then closes and resets",
    async (action) => {
      const user = userEvent.setup();
      const save = deferredSave();
      axios.post.mockReturnValueOnce(save.promise);
      const { onClose, invalidate } = renderDialog();
      await user.type(getMessage(), "Save the revised instructions");
      expect(getCommitButton()).toBeEnabled();
      await user.click(
        screen.getByRole("button", { name: action, exact: true }),
      );

      await waitFor(() =>
        expect(axios.post).toHaveBeenCalledExactlyOnceWith(COMMIT_URL, {
          message: "Save the revised instructions",
          set_default: action === DEFAULT_ACTION,
          is_draft: false,
          version_name: "v1",
        }),
      );
      expect(getCommitButton()).toBeDisabled();
      expect(getDefaultButton()).toBeDisabled();
      expect(screen.getAllByRole("progressbar")).toHaveLength(2);
      expect(onClose).not.toHaveBeenCalled();
      expect(invalidate).not.toHaveBeenCalled();

      await act(async () => save.resolve({ data: { result: {} } }));
      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
      );
      expect(onClose).toHaveBeenCalledTimes(1);
      expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
      expect(invalidate.mock.calls).toEqual([
        [{ queryKey: ["prompt-versions", "prompt-123"] }],
        [{ queryKey: ["prompt-latest-version", "prompt-123"] }],
      ]);

      await user.click(
        screen.getByRole("button", { name: "Reopen commit dialog" }),
      );
      expect(getMessage()).toHaveValue("");
      expect(getCommitButton()).toBeDisabled();
      expect(getDefaultButton()).toBeDisabled();
      expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    },
  );

  it("retains the message and restores the active appearance after a failed save, allowing retry", async () => {
    const user = userEvent.setup();
    const failedSave = deferredSave();
    const retry = deferredSave();
    axios.post
      .mockReturnValueOnce(failedSave.promise)
      .mockReturnValueOnce(retry.promise);
    const { onClose, invalidate } = renderDialog(true);
    await user.type(getMessage(), "Keep this message for retry");
    const activeColor = getComputedStyle(getCommitButton()).color;
    await user.click(getCommitButton());
    await waitFor(() => expect(getCommitButton()).toBeDisabled());
    await act(async () =>
      failedSave.reject(new Error("Synthetic save failure")),
    );

    await waitFor(() => expect(getCommitButton()).toBeEnabled());
    expect(getDefaultButton()).toBeEnabled();
    expect(getMessage()).toHaveValue("Keep this message for retry");
    expect(getComputedStyle(getCommitButton()).color).toBe(activeColor);
    expect(getCommitButton()).toHaveStyle({
      color: palette(mode).text.primary,
    });
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    expect(invalidate).not.toHaveBeenCalled();
    expect(enqueueSnackbar).not.toHaveBeenCalled();

    await user.click(getCommitButton());
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(2));
    expect(axios.post.mock.calls).toEqual(
      [1, 2].map(() => [
        COMMIT_URL,
        {
          message: "Keep this message for retry",
          set_default: true,
          is_draft: false,
          version_name: "v1",
        },
      ]),
    );
    expect(getCommitButton()).toBeDisabled();
    await act(async () => retry.resolve({ data: { result: {} } }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
