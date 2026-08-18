import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";

const { postMock } = vi.hoisted(() => ({
  postMock: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: { post: (...args) => postMock(...args) },
  endpoints: {
    keys: { generateSecretKey: "/accounts/key/generate_secret_key/" },
  },
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

import CreateApiKey from "../CreateApiKey";

const renderDialog = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <CreateApiKey open onClose={vi.fn()} refreshGrid={vi.fn()} />
    </QueryClientProvider>,
  );
};

describe("CreateApiKey expiry payload", () => {
  beforeEach(() => {
    postMock.mockReset();
    postMock.mockResolvedValue({
      data: { result: { api_key: "a", secret_key: "s" } },
    });
  });

  it("omits expires_at when no expiry is chosen", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.type(
      screen.getByPlaceholderText("Enter your key name"),
      "my-key",
    );
    await user.click(screen.getByRole("button", { name: /next/i }));

    await waitFor(() => expect(postMock).toHaveBeenCalledTimes(1));
    expect(postMock).toHaveBeenCalledWith(
      "/accounts/key/generate_secret_key/",
      {
        key_name: "my-key",
      },
    );
  });

  it("sends expires_at as ISO when a date is chosen", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.type(
      screen.getByPlaceholderText("Enter your key name"),
      "temp-key",
    );
    fireEvent.change(screen.getByLabelText("Expires (optional)"), {
      target: { value: "2026-12-01T15:30" },
    });
    await user.click(screen.getByRole("button", { name: /next/i }));

    await waitFor(() => expect(postMock).toHaveBeenCalledTimes(1));
    const payload = postMock.mock.calls[0][1];
    expect(payload.key_name).toBe("temp-key");
    expect(payload.expires_at).toBe(new Date("2026-12-01T15:30").toISOString());
  });
});
