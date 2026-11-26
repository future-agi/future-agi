import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const access = vi.hoisted(() => ({ canUnlockVersion: true }));
vi.mock("src/sections/simulate/environments/hooks/useSimulationAccess", () => ({
  default: () => ({
    canEdit: true,
    canRun: true,
    canUnlockVersion: access.canUnlockVersion,
  }),
}));
const snackbars = vi.hoisted(() => []);
vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn((message, options) =>
    snackbars.push({ message, options }),
  ),
  closeSnackbar: vi.fn(),
}));
const unlockVersion = vi.hoisted(() => vi.fn(async () => null));
vi.mock("src/api/simulate-environments/versions", async () => ({
  ...(await vi.importActual("src/api/simulate-environments/versions")),
  unlockVersion,
}));

const { default: useDeleteRefusal } = await import("../useDeleteRefusal");

// A delete refused because a version is locked, as the server sends it (409).
const LOCKED = {
  response: {
    status: 409,
    data: {
      error: "version_locked",
      message:
        "A workspace admin or owner must unlock version 2026.11.1 first.",
      retryable: false,
      locked_version_id: "lv-1",
      label: "2026.11.1",
    },
  },
};

const refuse = (error) => {
  const client = new QueryClient();
  const { result } = renderHook(() => useDeleteRefusal(), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
  result.current("env-1", error);
  return snackbars.at(-1);
};

beforeEach(() => {
  snackbars.length = 0;
  access.canUnlockVersion = true;
  unlockVersion.mockClear();
});

describe("useDeleteRefusal", () => {
  it("offers an admin Unlock version in a locked-version refusal, and unlocks it", async () => {
    const shown = refuse(LOCKED);

    expect(shown.message).toBe(
      "A workspace admin or owner must unlock version 2026.11.1 first.",
    );
    expect(shown.options.variant).toBe("error");
    render(shown.options.action("snack-1"));
    await userEvent.click(
      screen.getByRole("button", { name: "Unlock version 2026.11.1" }),
    );

    expect(unlockVersion).toHaveBeenCalledWith("env-1", "lv-1");
    await waitFor(() =>
      expect(snackbars.at(-1)).toMatchObject({
        message: "Version 2026.11.1 is unlocked.",
        options: { variant: "success" },
      }),
    );
  });

  it("offers a member no Unlock", () => {
    access.canUnlockVersion = false;

    const shown = refuse(LOCKED);

    expect(shown.message).toBe(
      "A workspace admin or owner must unlock version 2026.11.1 first.",
    );
    expect(shown.options.action).toBeUndefined();
  });

  it("offers no Unlock when the refusal names no label", () => {
    const shown = refuse({
      response: {
        ...LOCKED.response,
        data: { ...LOCKED.response.data, label: null },
      },
    });

    expect(shown.options.action).toBeUndefined();
  });

  it("shows any other refusal as it is", () => {
    const shown = refuse({
      response: { status: 409, data: { error: "run_live", message: "Busy." } },
    });

    expect(shown).toEqual({ message: "Busy.", options: { variant: "error" } });
  });
});
