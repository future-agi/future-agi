import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";

import CreateApiKey from "./CreateApiKey";

const mocks = vi.hoisted(() => ({
  copyToClipboard: vi.fn(),
  mutationData: null,
  mutate: vi.fn(),
  reset: vi.fn(),
}));

vi.mock("@tanstack/react-query", () => ({
  useMutation: (options) => ({
    data: mocks.mutationData,
    isPending: false,
    mutate: () => {
      mocks.mutationData = {
        data: {
          result: {
            api_key: "api-key",
            masked_api_key: "api-****",
            masked_secret_key: "secret-****",
            secret_key: "secret-key",
          },
        },
      };
      mocks.mutate();
      options?.onSuccess?.();
    },
    reset: mocks.reset,
  }),
}));

vi.mock("src/utils/axios", () => ({
  default: {
    post: vi.fn(),
  },
  endpoints: {
    keys: {
      generateSecretKey: "/accounts/key/generate_secret_key/",
    },
  },
}));

vi.mock("src/components/iconify", () => ({
  default: (props) => <span data-testid="iconify" {...props} />,
}));

vi.mock("src/components/svg-color", () => ({
  default: (props) => <span data-testid="svg-color" {...props} />,
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/utils/utils", () => ({
  copyToClipboard: mocks.copyToClipboard,
}));

describe("CreateApiKey onboarding defaults", () => {
  beforeEach(() => {
    mocks.mutationData = null;
    mocks.copyToClipboard.mockClear();
    mocks.mutate.mockClear();
    mocks.reset.mockClear();
  });

  it("prefills the key name from the onboarding handoff", () => {
    render(
      <CreateApiKey
        initialKeyName="Observe first trace"
        onClose={vi.fn()}
        open
        refreshGrid={vi.fn()}
      />,
    );

    expect(screen.getByRole("textbox")).toHaveValue("Observe first trace");
    expect(screen.getByRole("button", { name: /next/i })).toBeEnabled();
  });

  it("keeps the normal create dialog blank without an onboarding default", () => {
    render(<CreateApiKey onClose={vi.fn()} open refreshGrid={vi.fn()} />);

    expect(screen.getByRole("textbox")).toHaveValue("");
    expect(screen.getByRole("button", { name: /next/i })).toBeDisabled();
  });

  it("returns to trace setup after onboarding key creation", async () => {
    const onClose = vi.fn();

    render(
      <CreateApiKey
        completionHref="/dashboard/observe?setup=true&source=onboarding"
        initialKeyName="Observe first trace"
        onClose={onClose}
        open
        refreshGrid={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: /cancel/i })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: /next/i }));

    const returnAction = await screen.findByRole("link", {
      name: /back to trace setup/i,
    });
    expect(returnAction).toHaveAttribute(
      "href",
      "/dashboard/observe?setup=true&source=onboarding",
    );
    expect(returnAction).toHaveAttribute("aria-disabled", "true");
    expect(
      screen.getByText("Copy both keys before returning to trace setup."),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: /cancel/i })).toBeDisabled();

    await userEvent.click(
      screen.getByRole("button", { name: /copy api key/i }),
    );
    expect(mocks.copyToClipboard).toHaveBeenCalledWith("api-key");
    expect(returnAction).toHaveAttribute("aria-disabled", "true");

    await userEvent.click(
      screen.getByRole("button", { name: /copy secret key/i }),
    );
    expect(mocks.copyToClipboard).toHaveBeenCalledWith("secret-key");

    await waitFor(() => {
      expect(
        screen.getByRole("link", { name: /back to trace setup/i }),
      ).not.toHaveAttribute("aria-disabled", "true");
    });
    expect(screen.getByRole("button", { name: /cancel/i })).toBeEnabled();

    await userEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
