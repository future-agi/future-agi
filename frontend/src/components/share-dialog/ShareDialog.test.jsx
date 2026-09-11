import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { enqueueSnackbar } from "notistack";
import { render, screen, waitFor, userEvent } from "src/utils/test-utils";
import ShareDialog from "./ShareDialog";

const mocks = vi.hoisted(() => ({
  useGetSharedLinks: vi.fn(),
  useCreateSharedLink: vi.fn(),
  useUpdateSharedLink: vi.fn(),
  useAddSharedLinkAccess: vi.fn(),
  useRemoveSharedLinkAccess: vi.fn(),
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/api/shared-links", () => ({
  useGetSharedLinks: mocks.useGetSharedLinks,
  useCreateSharedLink: mocks.useCreateSharedLink,
  useUpdateSharedLink: mocks.useUpdateSharedLink,
  useAddSharedLinkAccess: mocks.useAddSharedLinkAccess,
  useRemoveSharedLinkAccess: mocks.useRemoveSharedLinkAccess,
}));

function renderDialog() {
  return render(
    <ShareDialog
      open
      onClose={vi.fn()}
      resourceType="trace"
      resourceId="trace-123"
    />,
  );
}

describe("ShareDialog", () => {
  let createMutate;
  let updateMutate;
  let addAccessMutate;
  let removeAccessMutate;

  beforeEach(() => {
    createMutate = vi.fn();
    updateMutate = vi.fn();
    addAccessMutate = vi.fn();
    removeAccessMutate = vi.fn();
    vi.clearAllMocks();

    mocks.useGetSharedLinks.mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
    });
    mocks.useCreateSharedLink.mockReturnValue({
      mutate: createMutate,
      isPending: false,
      data: null,
    });
    mocks.useUpdateSharedLink.mockReturnValue({
      mutate: updateMutate,
      isPending: false,
    });
    mocks.useAddSharedLinkAccess.mockReturnValue({
      mutate: addAccessMutate,
      isPending: false,
    });
    mocks.useRemoveSharedLinkAccess.mockReturnValue({
      mutate: removeAccessMutate,
      isPending: false,
    });
  });

  it("uses a just-created link for invite mutations before the list refetch lands", async () => {
    mocks.useCreateSharedLink.mockReturnValue({
      mutate: createMutate,
      isPending: false,
      data: {
        data: {
          result: {
            id: "link-created",
            token: "created-token",
            access_type: "restricted",
            access_list: [],
          },
        },
      },
    });

    renderDialog();

    await waitFor(() =>
      expect(createMutate).toHaveBeenCalledWith({
        resource_type: "trace",
        resource_id: "trace-123",
        access_type: "restricted",
      }),
    );
    expect(screen.getByText(/\/shared\/created-token/)).toBeInTheDocument();

    const user = userEvent.setup();
    await user.type(
      screen.getByPlaceholderText("name@email.com"),
      "viewer@example.com",
    );
    await user.click(screen.getByRole("button", { name: "Invite" }));

    expect(addAccessMutate).toHaveBeenCalledWith({
      linkId: "link-created",
      emails: ["viewer@example.com"],
    });
    expect(screen.getByText("People with access (1)")).toBeInTheDocument();
    expect(screen.getByText("viewer@example.com")).toBeInTheDocument();
  });

  it("does not optimistically add an invite when no tokenized link exists yet", async () => {
    mocks.useCreateSharedLink.mockReturnValue({
      mutate: createMutate,
      isPending: true,
      data: null,
    });

    renderDialog();

    const user = userEvent.setup();
    await user.type(
      screen.getByPlaceholderText("name@email.com"),
      "pending@example.com",
    );
    await user.keyboard("{Enter}");

    expect(addAccessMutate).not.toHaveBeenCalled();
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "Share link is still being generated",
      { variant: "warning" },
    );
    expect(screen.queryByText("pending@example.com")).not.toBeInTheDocument();
  });

  it("updates access mode through the created link before refetch", async () => {
    mocks.useCreateSharedLink.mockReturnValue({
      mutate: createMutate,
      isPending: false,
      data: {
        data: {
          result: {
            id: "link-created",
            token: "created-token",
            access_type: "restricted",
            access_list: [],
          },
        },
      },
    });

    renderDialog();

    const user = userEvent.setup();
    await user.click(
      screen.getByRole("button", { name: /Anyone with the link/i }),
    );

    expect(updateMutate).toHaveBeenCalledWith({
      id: "link-created",
      access_type: "public",
    });
  });

  it("removes server access through the active shared link id", async () => {
    mocks.useGetSharedLinks.mockReturnValue({
      data: [
        {
          id: "link-active",
          token: "active-token",
          access_type: "restricted",
          is_active: true,
          access_list: [
            {
              id: "access-viewer",
              email: "viewer@example.com",
            },
          ],
        },
      ],
      isLoading: false,
      isError: false,
    });

    renderDialog();

    const user = userEvent.setup();
    await user.click(
      screen.getByRole("button", {
        name: "Remove access for viewer@example.com",
      }),
    );

    expect(removeAccessMutate).toHaveBeenCalledWith({
      linkId: "link-active",
      accessId: "access-viewer",
    });
  });
});
