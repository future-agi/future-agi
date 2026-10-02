// TH-4565 regression coverage for ShareDialog link, clipboard and access state.
// Hook/transport states are controlled; this is component-level evidence only.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { enqueueSnackbar } from "notistack";
import { act, fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import ShareDialog from "./ShareDialog";

const mocks = vi.hoisted(() => ({
  useGetSharedLinks: vi.fn(),
  useCreateSharedLink: vi.fn(),
  useUpdateSharedLink: vi.fn(),
  useAddSharedLinkAccess: vi.fn(),
  useRemoveSharedLinkAccess: vi.fn(),
}));

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/api/shared-links", () => mocks);
vi.mock("src/components/iconify", () => ({ default: () => null }));

const DASHBOARD_PATH = "/dashboard/observe/project-1?traceId=trace-1";
const activeLink = {
  id: "link-1",
  token: "token-1",
  access_type: "restricted",
  is_active: true,
  access_list: [],
};

let writeText;
let createMutate;
let createReset;
let updateMutate;
let updateReset;
let refetch;

const linksState = (overrides = {}) => ({
  data: [activeLink],
  isLoading: false,
  isError: false,
  error: null,
  refetch,
  ...overrides,
});
const createState = (overrides = {}) => ({
  mutate: createMutate,
  reset: createReset,
  isPending: false,
  isError: false,
  error: null,
  data: null,
  ...overrides,
});
const updateState = (overrides = {}) => ({
  mutate: updateMutate,
  reset: updateReset,
  isPending: false,
  isError: false,
  ...overrides,
});

const dialog = (props = {}) => (
  <ShareDialog
    open
    onClose={vi.fn()}
    resourceType="trace"
    resourceId="trace-1"
    {...props}
  />
);

const copyButton = () =>
  screen.getByRole("button", { name: /^Copy$|^Copied$/ });
const publicOption = () =>
  screen.getByRole("button", { name: /Anyone with the link/i });
const restrictedOption = () =>
  screen.getByRole("button", { name: /^Restricted/i });
const sharedUrl = (token) => `${window.location.origin}/shared/${token}`;
const findReadyCopy = async () => {
  const button = await screen.findByRole("button", { name: "Copy" });
  await waitFor(() => expect(button).toBeEnabled());
  return button;
};

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", DASHBOARD_PATH);
  writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
  createMutate = vi.fn();
  createReset = vi.fn();
  updateMutate = vi.fn();
  updateReset = vi.fn();
  refetch = vi
    .fn()
    .mockResolvedValue({ data: [activeLink], isError: false, isSuccess: true });
  mocks.useGetSharedLinks.mockReturnValue(linksState());
  mocks.useCreateSharedLink.mockReturnValue(createState());
  mocks.useUpdateSharedLink.mockReturnValue(updateState());
  mocks.useAddSharedLinkAccess.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  });
  mocks.useRemoveSharedLinkAccess.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("ShareDialog trace link readiness (R1, R2, R6)", () => {
  it("AC01: copies the token URL, never the authenticated page URL", async () => {
    render(dialog());
    const button = await findReadyCopy();
    expect(screen.getByText(sharedUrl("token-1"))).toBeInTheDocument();
    expect(screen.queryByText(/dashboard\/observe/)).not.toBeInTheDocument();

    fireEvent.click(button);
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith(sharedUrl("token-1")),
    );
  });

  it("AC02 (D1): list failure disables copy, access and invite; no clipboard write or auto-create", async () => {
    mocks.useGetSharedLinks.mockReturnValue(
      linksState({ data: undefined, isError: true, error: new Error("boom") }),
    );
    render(dialog());

    expect(copyButton()).toBeDisabled();
    fireEvent.click(copyButton());
    expect(writeText).not.toHaveBeenCalled();
    expect(createMutate).not.toHaveBeenCalled();
    expect(screen.queryByText(window.location.href)).not.toBeInTheDocument();
    expect(publicOption()).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("button", { name: "Invite" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("AC02: list failure with retained cached data is still not ready", () => {
    mocks.useGetSharedLinks.mockReturnValue(
      linksState({ isError: true, error: new Error("stale") }),
    );
    render(dialog());

    expect(copyButton()).toBeDisabled();
    expect(publicOption()).toHaveAttribute("aria-disabled", "true");
    expect(createMutate).not.toHaveBeenCalled();
  });

  it("AC03 (D2): create failure shows a safe not-ready error with no dashboard URL or automatic retry", async () => {
    mocks.useGetSharedLinks.mockReturnValue(linksState({ data: [] }));
    const view = render(dialog());
    await waitFor(() => expect(createMutate).toHaveBeenCalledTimes(1));

    mocks.useCreateSharedLink.mockReturnValue(
      createState({ isError: true, error: new Error("<b>secret-token</b>") }),
    );
    view.rerender(dialog());

    expect(copyButton()).toBeDisabled();
    expect(screen.queryByText(window.location.href)).not.toBeInTheDocument();
    expect(screen.queryByText(/secret-token/)).not.toBeInTheDocument();
    expect(
      screen.getByText(/Couldn't create a share link/),
    ).toBeInTheDocument();
    expect(createMutate).toHaveBeenCalledTimes(1);

    view.rerender(dialog());
    expect(createMutate).toHaveBeenCalledTimes(1);
  });

  it("AC04: an id without a usable token stays not ready", () => {
    mocks.useGetSharedLinks.mockReturnValue(
      linksState({ data: [{ ...activeLink, token: "   " }] }),
    );
    render(dialog());

    expect(copyButton()).toBeDisabled();
    expect(screen.queryByText(window.location.href)).not.toBeInTheDocument();
    expect(createMutate).not.toHaveBeenCalled();
  });

  it("AC04/AC17: an inactive link is unavailable and is not silently recreated", async () => {
    mocks.useGetSharedLinks.mockReturnValue(
      linksState({ data: [{ ...activeLink, is_active: false }] }),
    );
    render(dialog());

    expect(copyButton()).toBeDisabled();
    expect(await screen.findByText(/no longer active/i)).toBeInTheDocument();
    expect(createMutate).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Recheck" })).toBeInTheDocument();
  });

  it("AC05/AC17: a healthy empty list creates exactly one restricted link, even across re-renders", async () => {
    mocks.useGetSharedLinks.mockReturnValue(linksState({ data: [] }));
    const view = render(dialog());

    await waitFor(() => expect(createMutate).toHaveBeenCalledTimes(1));
    expect(createMutate).toHaveBeenCalledWith({
      resource_type: "trace",
      resource_id: "trace-1",
      access_type: "restricted",
    });

    view.rerender(dialog());
    view.rerender(dialog());
    expect(createMutate).toHaveBeenCalledTimes(1);
    expect(
      createMutate.mock.calls.every(([v]) => v.access_type !== "public"),
    ).toBe(true);
  });
});

describe("ShareDialog retry (R2, R3)", () => {
  it("AC06: retry after create failure refetches first and reuses an existing link without a new POST", async () => {
    mocks.useGetSharedLinks.mockReturnValue(linksState({ data: [] }));
    const view = render(dialog());
    await waitFor(() => expect(createMutate).toHaveBeenCalledTimes(1));
    mocks.useCreateSharedLink.mockReturnValue(
      createState({ isError: true, error: new Error("lost") }),
    );
    view.rerender(dialog());
    refetch.mockClear();

    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
    await waitFor(() => expect(refetch).toHaveBeenCalledTimes(1));
    expect(createReset).toHaveBeenCalled();

    // The refetch found the committed link: render it and expect no second create.
    mocks.useGetSharedLinks.mockReturnValue(linksState());
    mocks.useCreateSharedLink.mockReturnValue(createState());
    view.rerender(dialog());
    await findReadyCopy();
    expect(createMutate).toHaveBeenCalledTimes(1);
  });

  it("AC07: a failed retry refetch never creates; a fresh empty result allows one restricted create", async () => {
    mocks.useGetSharedLinks.mockReturnValue(linksState({ data: [] }));
    const view = render(dialog());
    await waitFor(() => expect(createMutate).toHaveBeenCalledTimes(1));
    mocks.useCreateSharedLink.mockReturnValue(
      createState({ isError: true, error: new Error("lost") }),
    );
    view.rerender(dialog());
    refetch.mockClear();

    // Retry whose refetch fails: the error state is cleared but nothing is created.
    refetch.mockResolvedValueOnce({ data: undefined, isError: true });
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
    await waitFor(() => expect(refetch).toHaveBeenCalledTimes(1));
    mocks.useCreateSharedLink.mockReturnValue(createState());
    view.rerender(dialog());
    await act(async () => {});
    expect(createMutate).toHaveBeenCalledTimes(1);

    // Retry whose fresh empty read succeeds: exactly one more restricted create.
    mocks.useCreateSharedLink.mockReturnValue(
      createState({ isError: true, error: new Error("lost") }),
    );
    view.rerender(dialog());
    refetch.mockResolvedValueOnce({
      data: [],
      isError: false,
      isSuccess: true,
    });
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
    await waitFor(() => expect(refetch).toHaveBeenCalledTimes(2));
    mocks.useCreateSharedLink.mockReturnValue(createState());
    view.rerender(dialog());
    await waitFor(() => expect(createMutate).toHaveBeenCalledTimes(2));
    expect(createMutate.mock.calls[1][0]).toMatchObject({
      access_type: "restricted",
    });
  });

  it("AC08: known statuses map to safe copy and server text is never rendered", () => {
    const serverText =
      "<script>alert('x')</script> token=abc https://evil.example";
    mocks.useGetSharedLinks.mockReturnValue(
      linksState({
        data: undefined,
        isError: true,
        error: {
          message: serverText,
          response: { status: 401, data: serverText },
        },
      }),
    );
    const view = render(dialog());
    expect(screen.getByText(/session has expired/i)).toBeInTheDocument();
    expect(
      screen.queryByText(/evil\.example|token=abc|alert/),
    ).not.toBeInTheDocument();

    mocks.useGetSharedLinks.mockReturnValue(
      linksState({
        data: undefined,
        isError: true,
        error: { response: { status: 403 } },
      }),
    );
    view.rerender(dialog());
    expect(screen.getByText(/can't be shared from here/i)).toBeInTheDocument();

    mocks.useGetSharedLinks.mockReturnValue(
      linksState({
        data: undefined,
        isError: true,
        error: new Error("Network Error"),
      }),
    );
    view.rerender(dialog());
    expect(
      screen.getByText(/Couldn't load the share link/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Network Error/)).not.toBeInTheDocument();
  });

  it("AC09: retry works from the keyboard and disabled access options ignore Enter/Space", async () => {
    mocks.useGetSharedLinks.mockReturnValue(
      linksState({ data: undefined, isError: true, error: new Error("boom") }),
    );
    render(dialog());

    const retry = screen.getByRole("button", { name: "Retry" });
    retry.focus();
    fireEvent.keyDown(retry, { key: "Enter" });
    fireEvent.click(retry); // native button: keyboard Enter dispatches click
    await waitFor(() => expect(refetch).toHaveBeenCalled());

    fireEvent.keyDown(publicOption(), { key: "Enter" });
    fireEvent.keyDown(publicOption(), { key: " " });
    fireEvent.click(publicOption());
    expect(updateMutate).not.toHaveBeenCalled();
  });

  it("AC09: enabled access options respond to Enter", async () => {
    render(dialog());
    await findReadyCopy();

    fireEvent.keyDown(publicOption(), { key: "Enter" });
    expect(updateMutate).toHaveBeenCalledTimes(1);
    expect(updateMutate.mock.calls[0][0]).toEqual({
      id: "link-1",
      access_type: "public",
    });
  });
});

describe("ShareDialog clipboard lifecycle (R4, R7)", () => {
  it("AC10 (D3): a pending clipboard write reports nothing and blocks concurrent writes", async () => {
    writeText.mockReturnValue(new Promise(() => {}));
    render(dialog());
    const button = await findReadyCopy();

    fireEvent.click(button);
    fireEvent.click(button);
    await act(async () => {});
    expect(writeText).toHaveBeenCalledTimes(1);
    expect(enqueueSnackbar).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("button", { name: "Copied" }),
    ).not.toBeInTheDocument();
  });

  it("AC11: success is reported once after resolution, then resets", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(dialog());
    const button = await findReadyCopy();

    fireEvent.click(button);
    await screen.findByRole("button", { name: "Copied" });
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "Link copied!",
      expect.objectContaining({ variant: "success" }),
    );

    await act(async () => {
      vi.advanceTimersByTime(2100);
    });
    expect(screen.getByRole("button", { name: "Copy" })).toBeInTheDocument();
  });

  it("AC11: a rejected or missing clipboard produces only a failure and keeps the URL selectable", async () => {
    writeText.mockRejectedValue(new Error("denied"));
    const view = render(dialog());
    fireEvent.click(await findReadyCopy());

    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        expect.stringMatching(/Couldn't copy/),
        expect.objectContaining({ variant: "warning" }),
      ),
    );
    expect(enqueueSnackbar).not.toHaveBeenCalledWith(
      "Link copied!",
      expect.anything(),
    );
    expect(screen.getByText(sharedUrl("token-1"))).toHaveStyle({
      userSelect: "all",
    });

    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: undefined,
    });
    enqueueSnackbar.mockClear();
    view.rerender(dialog());
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        expect.stringMatching(/Clipboard isn't available/),
        expect.objectContaining({ variant: "warning" }),
      ),
    );
  });

  it("AC12/AC18: a copy that resolves after close/reopen does not report on the new dialog", async () => {
    let resolveCopy;
    writeText.mockReturnValue(
      new Promise((resolve) => {
        resolveCopy = resolve;
      }),
    );
    const view = render(dialog());
    fireEvent.click(await findReadyCopy());

    view.rerender(dialog({ open: false }));
    view.rerender(dialog({ open: true }));
    await act(async () => {
      resolveCopy();
    });
    await act(async () => {});

    expect(enqueueSnackbar).not.toHaveBeenCalledWith(
      "Link copied!",
      expect.anything(),
    );
    expect(
      screen.queryByRole("button", { name: "Copied" }),
    ).not.toBeInTheDocument();
    expect(createReset).toHaveBeenCalled();
    expect(updateReset).toHaveBeenCalled();
  });
});

describe("ShareDialog confirmed vs pending access (R5, R6)", () => {
  it("AC13: a pending change keeps the last confirmed selection and disables copy/access/invite", async () => {
    render(dialog());
    await findReadyCopy();

    fireEvent.click(publicOption());
    expect(updateMutate).toHaveBeenCalledTimes(1);
    expect(updateMutate.mock.calls[0][0]).toEqual({
      id: "link-1",
      access_type: "public",
    });

    expect(restrictedOption()).toHaveAttribute("aria-pressed", "true");
    expect(publicOption()).toHaveAttribute("aria-pressed", "false");
    expect(publicOption()).toHaveAttribute("aria-busy", "true");
    expect(copyButton()).toBeDisabled();
    expect(screen.getByRole("button", { name: "Invite" })).toBeDisabled();

    fireEvent.click(publicOption());
    fireEvent.click(restrictedOption());
    expect(updateMutate).toHaveBeenCalledTimes(1);
  });

  it("AC14: a matching successful response confirms the new mode before any list refetch", async () => {
    render(dialog());
    await findReadyCopy();

    fireEvent.click(publicOption());
    const [, callbacks] = updateMutate.mock.calls[0];
    await act(async () => {
      callbacks.onSuccess({
        data: { result: { id: "link-1", access_type: "public" } },
      });
    });

    expect(publicOption()).toHaveAttribute("aria-pressed", "true");
    expect(restrictedOption()).toHaveAttribute("aria-pressed", "false");
    expect(await findReadyCopy()).toBeEnabled();
  });

  it("AC14: a list result older than the acknowledgement cannot reverse it; a newer one is authoritative", async () => {
    mocks.useGetSharedLinks.mockReturnValue(linksState({ dataUpdatedAt: 1 }));
    const view = render(dialog());
    await findReadyCopy();

    fireEvent.click(publicOption());
    const [, callbacks] = updateMutate.mock.calls[0];
    await act(async () => {
      callbacks.onSuccess({
        data: { result: { id: "link-1", access_type: "public" } },
      });
    });

    // Older (pre-mutation) read still says restricted: acknowledgement wins.
    view.rerender(dialog());
    expect(publicOption()).toHaveAttribute("aria-pressed", "true");

    // A read that lands after the acknowledgement is the server truth.
    mocks.useGetSharedLinks.mockReturnValue(
      linksState({ dataUpdatedAt: Date.now() + 60_000 }),
    );
    view.rerender(dialog());
    expect(restrictedOption()).toHaveAttribute("aria-pressed", "true");
  });

  it("AC15/AC16 (D4): a rejected update never asserts rollback; it rechecks and adopts the server state", async () => {
    refetch.mockResolvedValueOnce({
      data: [activeLink],
      isError: false,
      isSuccess: true,
    }); // reopen check
    refetch.mockResolvedValueOnce({
      data: [{ ...activeLink, access_type: "public" }],
      isError: false,
      isSuccess: true,
    });
    const view = render(dialog());
    await findReadyCopy();

    fireEvent.click(publicOption());
    const [, callbacks] = updateMutate.mock.calls[0];
    await act(async () => {
      callbacks.onError(new Error("fixture forbidden"));
    });

    expect(screen.queryByText(/fixture forbidden/)).not.toBeInTheDocument();
    expect(restrictedOption()).toHaveAttribute("aria-pressed", "true");
    await waitFor(() => expect(refetch).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        expect.stringMatching(/Couldn't confirm the access change/),
        expect.objectContaining({ variant: "warning" }),
      ),
    );
    expect(enqueueSnackbar).not.toHaveBeenCalledWith(
      expect.stringMatching(/fixture forbidden/),
      expect.anything(),
    );

    // Server actually committed public: the reread wins, no compensating PATCH.
    mocks.useGetSharedLinks.mockReturnValue(
      linksState({ data: [{ ...activeLink, access_type: "public" }] }),
    );
    view.rerender(dialog());
    await waitFor(() =>
      expect(publicOption()).toHaveAttribute("aria-pressed", "true"),
    );
    expect(updateMutate).toHaveBeenCalledTimes(1);
  });

  it("AC16: when the recheck also fails, access is unknown and copy stays disabled until a successful read", async () => {
    refetch.mockResolvedValueOnce({
      data: [activeLink],
      isError: false,
      isSuccess: true,
    }); // reopen check
    refetch.mockResolvedValueOnce({ data: undefined, isError: true });
    render(dialog());
    await findReadyCopy();

    fireEvent.click(publicOption());
    const [, callbacks] = updateMutate.mock.calls[0];
    await act(async () => {
      callbacks.onError(new Error("lost response"));
    });

    await screen.findByText(/couldn't be confirmed/i);
    expect(copyButton()).toBeDisabled();
    expect(publicOption()).toHaveAttribute("aria-disabled", "true");
    expect(updateMutate).toHaveBeenCalledTimes(1);

    refetch.mockResolvedValueOnce({
      data: [activeLink],
      isError: false,
      isSuccess: true,
    });
    fireEvent.click(screen.getByRole("button", { name: "Recheck" }));
    expect(await findReadyCopy()).toBeEnabled();
  });
});

describe("ShareDialog caller compatibility (R8)", () => {
  const voiceFallback = `${window.location.origin}/dashboard/observe/project-1/voice/trace-1`;

  it("AC20: an explicit caller fallback is shown and copyable when no token exists", async () => {
    mocks.useGetSharedLinks.mockReturnValue(linksState({ data: [] }));
    mocks.useCreateSharedLink.mockReturnValue(
      createState({ isError: true, error: new Error("lost") }),
    );
    render(dialog({ fallbackShareUrl: voiceFallback }));

    expect(await screen.findByText(voiceFallback)).toBeInTheDocument();
    fireEvent.click(await findReadyCopy());
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(voiceFallback));
    expect(publicOption()).toHaveAttribute("aria-disabled", "true");
  });

  it("AC20: a ready token takes precedence over the caller fallback", async () => {
    render(dialog({ fallbackShareUrl: voiceFallback }));
    await findReadyCopy();
    expect(screen.getByText(sharedUrl("token-1"))).toBeInTheDocument();
    expect(screen.queryByText(voiceFallback)).not.toBeInTheDocument();
  });

  it("AC20: the implicit current-page fallback is gone for callers without one", () => {
    mocks.useGetSharedLinks.mockReturnValue(linksState({ data: [] }));
    mocks.useCreateSharedLink.mockReturnValue(
      createState({ isError: true, error: new Error("lost") }),
    );
    render(dialog({ resourceType: "project", resourceId: "project-1" }));
    expect(screen.queryByText(window.location.href)).not.toBeInTheDocument();
    expect(copyButton()).toBeDisabled();
  });
});

describe("ShareDialog context boundaries (R7)", () => {
  it("AC19: switching resources clears copied/pending state and rereads before enabling actions", async () => {
    const view = render(dialog());
    fireEvent.click(await findReadyCopy());
    await screen.findByRole("button", { name: "Copied" });
    fireEvent.click(publicOption());
    expect(updateMutate).toHaveBeenCalledTimes(1);

    refetch.mockClear();
    view.rerender(dialog({ resourceId: "trace-2" }));
    expect(
      screen.queryByRole("button", { name: "Copied" }),
    ).not.toBeInTheDocument();
    expect(publicOption()).not.toHaveAttribute("aria-busy", "true");
    expect(createReset).toHaveBeenCalled();
    expect(updateReset).toHaveBeenCalled();
    await waitFor(() => expect(refetch).toHaveBeenCalledTimes(1));
    expect(await findReadyCopy()).toBeEnabled();
  });

  it("AC18: reopening after a create failure resets creation state and reads the server first", async () => {
    mocks.useGetSharedLinks.mockReturnValue(linksState({ data: [] }));
    mocks.useCreateSharedLink.mockReturnValue(
      createState({ isError: true, error: new Error("lost") }),
    );
    const view = render(dialog());
    expect(
      screen.getByText(/Couldn't create a share link/),
    ).toBeInTheDocument();

    view.rerender(dialog({ open: false }));
    expect(createReset).toHaveBeenCalled();

    mocks.useCreateSharedLink.mockReturnValue(createState());
    mocks.useGetSharedLinks.mockReturnValue(linksState());
    refetch.mockClear();
    view.rerender(dialog({ open: true }));
    await waitFor(() => expect(refetch).toHaveBeenCalledTimes(1));
    expect(await findReadyCopy()).toBeEnabled();
    expect(
      screen.queryByText(/Couldn't create a share link/),
    ).not.toBeInTheDocument();
  });
});
