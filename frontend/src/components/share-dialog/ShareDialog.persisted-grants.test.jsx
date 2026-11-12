// OBS-SW-18: component-level persisted ACL regressions; transport is mocked.
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { enqueueSnackbar } from "notistack";
import { act, fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import ShareDialog from "./ShareDialog";

const mocks = vi.hoisted(() => ({
  useGetSharedLinks: vi.fn(),
  useGetSharedLinkDetail: vi.fn(),
  useCreateSharedLink: vi.fn(),
  useUpdateSharedLink: vi.fn(),
  useAddSharedLinkAccess: vi.fn(),
  useRemoveSharedLinkAccess: vi.fn(),
}));
vi.mock("src/api/shared-links", () => mocks);
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/iconify", () => ({ default: () => null }));

const link = {
  id: "link-1",
  token: "token-1",
  access_type: "restricted",
  is_active: true,
  access_count: 1,
}; // List representation deliberately has no access_list.
const viewer = { id: "grant-viewer", email: "viewer@example.com" };
const newcomer = { id: "grant-new", email: "new@example.com" };
let addAccess;
let removeAccess;
let refetchDetail;
let detailState;

const dialog = (props = {}) => (
  <ShareDialog
    open
    onClose={vi.fn()}
    resourceType="trace"
    resourceId="trace-1"
    {...props}
  />
);
const removal = (email) =>
  screen.getByRole("button", { name: `Remove access for ${email}` });
const response = (grants) => ({ data: { status: "success", result: grants } });
const readResult = (grants, id = link.id) => ({
  data: { id, access_list: grants },
  isSuccess: true,
  isError: false,
});
const ready = async () => {
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Copy" })).toBeEnabled(),
  );
};
const invite = (email = newcomer.email) => {
  fireEvent.change(screen.getByPlaceholderText("name@email.com"), {
    target: { value: email },
  });
  fireEvent.click(screen.getByRole("button", { name: "Invite" }));
};
const settle = async (mutation, callback, value) => {
  await act(async () => {
    await mutation.mock.calls[mutation.mock.calls.length - 1][1][callback](
      value,
    );
  });
};

beforeEach(() => {
  vi.clearAllMocks();
  addAccess = vi.fn();
  removeAccess = vi.fn();
  refetchDetail = vi.fn().mockResolvedValue(readResult([viewer]));
  detailState = {
    ...readResult([viewer]),
    dataUpdatedAt: 1,
    refetch: refetchDetail,
  };
  mocks.useGetSharedLinks.mockReturnValue({
    data: [link],
    isLoading: false,
    isError: false,
    refetch: vi.fn().mockResolvedValue({ data: [link], isSuccess: true }),
  });
  mocks.useGetSharedLinkDetail.mockImplementation(() => detailState);
  mocks.useCreateSharedLink.mockReturnValue({
    mutate: vi.fn(),
    reset: vi.fn(),
  });
  mocks.useUpdateSharedLink.mockReturnValue({
    mutate: vi.fn(),
    reset: vi.fn(),
  });
  mocks.useAddSharedLinkAccess.mockReturnValue({ mutate: addAccess });
  mocks.useRemoveSharedLinkAccess.mockReturnValue({ mutate: removeAccess });
});

describe("OBS-SW-18 persisted grants", () => {
  it("ACL01: reads detail ACL for a list-only link and deletes the persisted access ID", async () => {
    render(dialog());
    await ready();
    expect(mocks.useGetSharedLinkDetail).toHaveBeenCalledWith("link-1");
    expect(screen.getByText("People with access (1)")).toBeInTheDocument();
    fireEvent.click(removal(viewer.email));
    expect(removeAccess).toHaveBeenCalledWith(
      { linkId: "link-1", accessId: "grant-viewer" },
      expect.objectContaining({
        onSuccess: expect.any(Function),
        onError: expect.any(Function),
      }),
    );
    expect(screen.getByText(viewer.email)).toBeInTheDocument();
    expect(removal(viewer.email)).toBeDisabled();
    await settle(
      removeAccess,
      "onSuccess",
      response({ message: "Access removed" }),
    );
    expect(screen.queryByText(viewer.email)).not.toBeInTheDocument();
  });

  it("ACL02: reconciles POST IDs before a detail refetch and revokes the returned grant", async () => {
    render(dialog());
    await ready();
    invite();
    expect(enqueueSnackbar).not.toHaveBeenCalledWith(
      `Shared with ${newcomer.email}`,
      expect.anything(),
    );
    await settle(addAccess, "onSuccess", response([newcomer]));
    expect(screen.getByText("People with access (2)")).toBeInTheDocument();
    expect(screen.queryByText("Pending invitation")).not.toBeInTheDocument();
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      `Shared with ${newcomer.email}`,
      expect.objectContaining({ variant: "success" }),
    );
    fireEvent.click(removal(newcomer.email));
    expect(removeAccess.mock.calls[0][0]).toEqual({
      linkId: "link-1",
      accessId: "grant-new",
    });
    await settle(
      removeAccess,
      "onSuccess",
      response({ message: "Access removed" }),
    );
    expect(screen.queryByText(newcomer.email)).not.toBeInTheDocument();
    expect(screen.getByText(viewer.email)).toBeInTheDocument();
  });

  it("ACL03: pending invitations are not access grants and serialize ACL writes", async () => {
    render(dialog());
    await ready();
    invite();
    expect(screen.getByText(newcomer.email)).toBeInTheDocument();
    expect(screen.getByText("Pending invitation")).toBeInTheDocument();
    expect(screen.getByText("People with access (1)")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", {
        name: `Remove access for ${newcomer.email}`,
      }),
    ).not.toBeInTheDocument();
    expect(removal(viewer.email)).toBeDisabled();
    invite("second@example.com");
    fireEvent.keyDown(screen.getByPlaceholderText("name@email.com"), {
      key: "Enter",
    });
    fireEvent.click(removal(viewer.email));
    expect(addAccess).toHaveBeenCalledTimes(1);
    expect(removeAccess).not.toHaveBeenCalled();
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("ACL04: failed invitations reread ACL, do not claim success, and leave no phantom grant", async () => {
    render(dialog());
    await ready();
    invite();
    await settle(addAccess, "onError", new Error("private server detail"));
    expect(refetchDetail).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(newcomer.email)).not.toBeInTheDocument();
    expect(screen.queryByText("Pending invitation")).not.toBeInTheDocument();
    expect(enqueueSnackbar).not.toHaveBeenCalledWith(
      `Shared with ${newcomer.email}`,
      expect.anything(),
    );
    expect(screen.queryByText(/private server detail/)).not.toBeInTheDocument();
    invite();
    expect(addAccess).toHaveBeenCalledTimes(2);
  });

  it("ACL05: duplicate POST with an empty created list resolves its existing persisted ID", async () => {
    refetchDetail.mockResolvedValue(readResult([viewer, newcomer]));
    render(dialog());
    await ready();
    invite();
    await settle(addAccess, "onSuccess", response([]));
    expect(refetchDetail).toHaveBeenCalledTimes(1);
    fireEvent.click(removal(newcomer.email));
    expect(removeAccess.mock.calls[0][0]).toEqual({
      linkId: "link-1",
      accessId: "grant-new",
    });
  });

  it("ACL06: lost POST response can recover a committed grant from detail", async () => {
    refetchDetail.mockResolvedValue(readResult([viewer, newcomer]));
    render(dialog());
    await ready();
    invite();
    await settle(addAccess, "onError", new Error("lost response"));
    fireEvent.click(removal(newcomer.email));
    expect(removeAccess.mock.calls[0][0]).toEqual({
      linkId: "link-1",
      accessId: "grant-new",
    });
  });

  it("ACL07: failed reconciliation stays unconfirmed and can be rechecked without another POST", async () => {
    refetchDetail.mockResolvedValueOnce({
      isError: true,
      data: { id: link.id, access_list: [newcomer] },
    });
    render(dialog());
    await ready();
    invite();
    await settle(addAccess, "onSuccess", response([]));
    expect(screen.getByText("Invitation not confirmed")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", {
        name: `Remove access for ${newcomer.email}`,
      }),
    ).not.toBeInTheDocument();
    expect(enqueueSnackbar).not.toHaveBeenCalledWith(
      `Shared with ${newcomer.email}`,
      expect.anything(),
    );
    refetchDetail.mockResolvedValue(readResult([viewer, newcomer]));
    fireEvent.click(screen.getByRole("button", { name: "Recheck invitation" }));
    await waitFor(() => expect(removal(newcomer.email)).toBeEnabled());
    expect(addAccess).toHaveBeenCalledTimes(1);
  });

  it("ACL08: unsuccessful DELETE retains the grant and permits retry", async () => {
    render(dialog());
    await ready();
    fireEvent.click(removal(viewer.email));
    await settle(removeAccess, "onError", new Error("lost response"));
    expect(screen.getByText(viewer.email)).toBeInTheDocument();
    expect(removal(viewer.email)).toBeEnabled();
    expect(enqueueSnackbar).not.toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ variant: "success" }),
    );
    fireEvent.click(removal(viewer.email));
    expect(removeAccess).toHaveBeenCalledTimes(2);
  });

  it("ACL09: newer authoritative detail supersedes acknowledgements and deduplicates grants", async () => {
    const view = render(dialog());
    await ready();
    invite();
    await settle(addAccess, "onSuccess", response([newcomer]));
    detailState = {
      ...detailState,
      data: { id: link.id, access_list: [viewer, newcomer] },
      dataUpdatedAt: Date.now() + 60_000,
    };
    view.rerender(dialog());
    expect(screen.getAllByText(newcomer.email)).toHaveLength(1);
    detailState = {
      ...detailState,
      data: { id: link.id, access_list: [viewer] },
      dataUpdatedAt: Date.now() + 120_000,
    };
    view.rerender(dialog());
    expect(screen.queryByText(newcomer.email)).not.toBeInTheDocument();
  });

  it.each(["onSuccess", "onError"])(
    "ACL10: ignores stale invite %s after resource change",
    async (callback) => {
      const view = render(dialog());
      await ready();
      invite();
      const callbacks = addAccess.mock.calls[0][1];
      view.rerender(dialog({ resourceId: "trace-2" }));
      await ready();
      expect(screen.queryByText(newcomer.email)).not.toBeInTheDocument();
      expect(screen.getByPlaceholderText("name@email.com")).toHaveValue("");
      await act(async () => {
        await callbacks[callback](response([newcomer]));
      });
      expect(screen.queryByText(newcomer.email)).not.toBeInTheDocument();
      expect(refetchDetail).not.toHaveBeenCalled();
      expect(enqueueSnackbar).not.toHaveBeenCalled();
    },
  );

  it("ACL11: close/reopen clears pending state and stale completion cannot unlock a new invitation", async () => {
    const view = render(dialog());
    await ready();
    invite();
    const oldCallbacks = addAccess.mock.calls[0][1];
    view.rerender(dialog({ open: false }));
    expect(mocks.useGetSharedLinkDetail).toHaveBeenLastCalledWith(null);
    view.rerender(dialog());
    await ready();
    invite("second@example.com");
    await act(async () => {
      await oldCallbacks.onSuccess(response([newcomer]));
    });
    expect(screen.queryByText(newcomer.email)).not.toBeInTheDocument();
    expect(screen.getByText("Pending invitation")).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("name@email.com"), {
      target: { value: "third@example.com" },
    });
    fireEvent.keyDown(screen.getByPlaceholderText("name@email.com"), {
      key: "Enter",
    });
    expect(addAccess).toHaveBeenCalledTimes(2);
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("ACL12: a late reconciliation read cannot populate a different resource", async () => {
    let resolveRead;
    refetchDetail.mockReturnValue(
      new Promise((resolve) => {
        resolveRead = resolve;
      }),
    );
    const view = render(dialog());
    await ready();
    invite();
    let completion;
    act(() => {
      completion = addAccess.mock.calls[0][1].onSuccess(response([]));
    });
    view.rerender(dialog({ resourceId: "trace-2" }));
    await act(async () => {
      resolveRead(readResult([newcomer]));
      await completion;
    });
    expect(screen.queryByText(newcomer.email)).not.toBeInTheDocument();
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("ACL13: selected-link changes clear pending work and ignore detail for another link", async () => {
    const view = render(dialog());
    await ready();
    invite();
    const oldCallbacks = addAccess.mock.calls[0][1];
    const nextLink = { ...link, id: "link-2", token: "token-2" };
    mocks.useGetSharedLinks.mockReturnValue({
      data: [nextLink],
      isLoading: false,
      isError: false,
    });
    view.rerender(dialog());
    expect(mocks.useGetSharedLinkDetail).toHaveBeenLastCalledWith("link-2");
    expect(screen.queryByText(viewer.email)).not.toBeInTheDocument();
    await act(async () => {
      await oldCallbacks.onSuccess(response([newcomer]));
    });
    expect(screen.queryByText(newcomer.email)).not.toBeInTheDocument();
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("ACL14: duplicate detection normalizes persisted email casing", async () => {
    detailState = {
      ...detailState,
      data: {
        id: link.id,
        access_list: [{ ...viewer, email: "Viewer@Example.com" }],
      },
    };
    render(dialog());
    await ready();
    invite("viewer@example.com");
    expect(addAccess).not.toHaveBeenCalled();
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "Already shared with this email",
      { variant: "info" },
    );
  });

  it.each(["onSuccess", "onError"])(
    "ACL16: stale DELETE %s cannot change or unlock the next resource",
    async (callback) => {
      const view = render(dialog());
      await ready();
      fireEvent.click(removal(viewer.email));
      const oldCallbacks = removeAccess.mock.calls[0][1];
      view.rerender(dialog({ resourceId: "trace-2" }));
      await ready();
      fireEvent.click(removal(viewer.email));
      await act(async () => {
        await oldCallbacks[callback](response({ message: "Access removed" }));
      });
      expect(screen.getByText(viewer.email)).toBeInTheDocument();
      expect(removal(viewer.email)).toBeDisabled();
      fireEvent.click(removal(viewer.email));
      expect(removeAccess).toHaveBeenCalledTimes(2);
      expect(refetchDetail).not.toHaveBeenCalled();
      expect(enqueueSnackbar).not.toHaveBeenCalled();
    },
  );

  it("ACL17: detail loading/error never substitutes a list-only ACL and can be retried", async () => {
    detailState = { ...detailState, data: undefined, isLoading: true };
    const view = render(dialog());
    await ready();
    expect(
      screen.queryByText("People with access (1)"),
    ).not.toBeInTheDocument();
    detailState = { ...detailState, isLoading: false, isError: true };
    view.rerender(dialog());
    expect(
      screen.getByText("Couldn't load people with access."),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Recheck access" }));
    expect(refetchDetail).toHaveBeenCalledTimes(1);
    detailState = { ...detailState, ...readResult([viewer]) };
    view.rerender(dialog());
    expect(removal(viewer.email)).toBeEnabled();
  });

  it("ACL18: unmounted invitation callbacks cannot notify or reread", async () => {
    const view = render(dialog());
    await ready();
    invite();
    const callbacks = addAccess.mock.calls[0][1];
    view.unmount();
    await act(async () => {
      await callbacks.onSuccess(response([newcomer]));
      await callbacks.onError(new Error("lost response"));
    });
    expect(refetchDetail).not.toHaveBeenCalled();
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("ACL19: no selected link leaves the detail query disabled without reading an absent ACL", () => {
    mocks.useGetSharedLinks.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
    });
    detailState = { ...detailState, data: undefined };
    render(dialog());
    expect(mocks.useGetSharedLinkDetail).toHaveBeenLastCalledWith(null);
    expect(screen.getByRole("button", { name: "Copy" })).toBeDisabled();
    expect(screen.queryByText(/People with access/)).not.toBeInTheDocument();
    invite();
    fireEvent.keyDown(screen.getByPlaceholderText("name@email.com"), {
      key: "Enter",
    });
    expect(addAccess).not.toHaveBeenCalled();
    expect(screen.queryByText("Pending invitation")).not.toBeInTheDocument();
  });

  it("ACL15: an empty or wrong-link reconciliation never invents a grant", async () => {
    refetchDetail.mockResolvedValue(readResult([newcomer], "different-link"));
    render(dialog());
    await ready();
    invite();
    await settle(addAccess, "onSuccess", response([]));
    expect(screen.getByText("Invitation not confirmed")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", {
        name: `Remove access for ${newcomer.email}`,
      }),
    ).not.toBeInTheDocument();
    refetchDetail.mockResolvedValue(readResult([]));
    fireEvent.click(screen.getByRole("button", { name: "Recheck invitation" }));
    await waitFor(() =>
      expect(screen.queryByText(newcomer.email)).not.toBeInTheDocument(),
    );
    expect(enqueueSnackbar).not.toHaveBeenCalledWith(
      `Shared with ${newcomer.email}`,
      expect.anything(),
    );
  });
});
