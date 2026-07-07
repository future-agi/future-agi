import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createTheme } from "@mui/material/styles";
import KeyCard from "./KeyCard";

const mockPost = vi.fn();

vi.mock("src/utils/axios", () => ({
  default: { post: (...args) => mockPost(...args) },
  endpoints: {
    develop: {
      apiKey: {
        create: "/model-hub/api-keys/",
        update: "/model-hub/api-keys/",
      },
    },
  },
}));

vi.mock("src/components/snackbar", () => ({
  enqueueSnackbar: vi.fn(),
}));

// Provider logo pulls in react-lazy-load-image-component, which does not render
// under jsdom and is irrelevant to the save behavior under test.
vi.mock("src/components/image", () => ({
  default: () => null,
}));

// The card renders a "key configured" check icon styled with palette.green,
// which the bare test theme does not define.
const theme = createTheme({
  palette: {
    mode: "light",
    primary: { main: "#1976d2" },
    green: { 400: "#22c55e" },
  },
});

const INVALID_KEY_MESSAGE = "Invalid API key, please type in the right one.";

const OPENAI_DATA = {
  provider: "openai",
  display_name: "Open AI",
  type: "text",
  maskedKey: "",
  hasKey: false,
  logoUrl: "https://example.test/openai.png",
};

function renderCard(props = {}) {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={qc}>
      <KeyCard data={OPENAI_DATA} onDeleteClick={vi.fn()} {...props} />
    </QueryClientProvider>,
    { theme },
  );
}

describe("KeyCard — invalid API key surfacing", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows the inline error when the provider rejects the key on save", async () => {
    mockPost.mockRejectedValue({
      message: INVALID_KEY_MESSAGE,
      statusCode: 400,
    });

    renderCard();

    const input = screen.getByRole("textbox");
    await userEvent.type(input, "totally-wrong-key");
    await userEvent.click(screen.getByRole("button", { name: /save/i }));

    expect(await screen.findByText(INVALID_KEY_MESSAGE)).toBeInTheDocument();
    expect(mockPost).toHaveBeenCalledWith(
      "/model-hub/api-keys/",
      expect.objectContaining({ provider: "openai", key: "totally-wrong-key" }),
    );
  });

  it("does not show the error when the save succeeds", async () => {
    mockPost.mockResolvedValue({ data: { status: true, result: {} } });

    renderCard();

    const input = screen.getByRole("textbox");
    await userEvent.type(input, "sk-valid-key");
    await userEvent.click(screen.getByRole("button", { name: /save/i }));

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByText(INVALID_KEY_MESSAGE)).not.toBeInTheDocument();
  });
});
