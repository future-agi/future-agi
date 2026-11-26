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
const { environmentVersionsKey, lockPreviewKey } = await import(
  "src/api/simulate-environments/versions"
);

// A delete refused because a version is locked, as the server sends it (409).
const LOCKED = {
  response: {
    status: 409,
    data: {
      error: "version_locked",
      message:
        "Lock 2026.11.1 covers this version. A workspace admin or owner must unlock it first.",
      retryable: false,
      locked_version_id: "lv-1",
      label: "2026.11.1",
    },
  },
};

// The unlock's answer: every version and the locked-version history.
const UNLOCKED = {
  agent: [],
  environment: [],
  suite: [],
  locked_versions: [
    {
      id: "lv-1",
      label: "2026.11.1",
      environment_label: "v3",
      suite_label: "v2",
      unlocked_at: "2026-11-26T10:00:00Z",
    },
  ],
};

let client;
const refuse = (error) => {
  client = new QueryClient();
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
  it("offers an admin Unlock versions in a locked-version refusal, and unlocks it", async () => {
    unlockVersion.mockResolvedValueOnce(UNLOCKED);
    const shown = refuse(LOCKED);

    expect(shown.message).toBe(
      "Lock 2026.11.1 covers this version. A workspace admin or owner must unlock it first.",
    );
    expect(shown.options.variant).toBe("error");
    render(shown.options.action("snack-1"));
    await userEvent.click(
      screen.getByRole("button", { name: "Unlock versions" }),
    );

    expect(unlockVersion).toHaveBeenCalledWith("env-1", "lv-1");
    await waitFor(() =>
      expect(snackbars.at(-1)).toMatchObject({
        message: "Unlocked environment v3, scenarios v2 (lock 2026.11.1).",
        options: { variant: "success" },
      }),
    );
  });

  it("asks for the lock preview again after the unlock", async () => {
    unlockVersion.mockResolvedValueOnce(UNLOCKED);
    const shown = refuse(LOCKED);
    client.setQueryData(lockPreviewKey("env-1"), { can_lock: false });
    render(shown.options.action("snack-1"));

    await userEvent.click(
      screen.getByRole("button", { name: "Unlock versions" }),
    );

    await waitFor(() =>
      expect(client.getQueryState(lockPreviewKey("env-1")).isInvalidated).toBe(
        true,
      ),
    );
    expect(client.getQueryData(environmentVersionsKey("env-1"))).toEqual(
      UNLOCKED,
    );
  });

  it("offers a member no Unlock", () => {
    access.canUnlockVersion = false;

    const shown = refuse(LOCKED);

    expect(shown.message).toBe(
      "Lock 2026.11.1 covers this version. A workspace admin or owner must unlock it first.",
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
