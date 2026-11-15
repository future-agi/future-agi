import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { palette } from "src/theme/palette";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get, post: vi.fn(), delete: vi.fn() },
  endpoints: {
    develop: { apiKey: { status: "/api-keys/status/" } },
    settings: { customModal: { getCustomModal: "/custom-models/" } },
  },
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/utils/Mixpanel", () => ({ trackEvent: vi.fn(), Events: {} }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/develop-detail/Common/ConfigureKeys/KeyCard", () => ({
  // eslint-disable-next-line react/prop-types
  default: ({ data }) => <div data-testid="provider">{data?.display_name}</div>,
}));
vi.mock("src/components/custom-model-dropdown/CloudProviderCard", () => ({
  // eslint-disable-next-line react/prop-types
  default: ({ data }) => <div data-testid="provider">{data?.display_name}</div>,
}));
vi.mock("src/components/custom-model-dropdown/CustomModalKeyCard", () => ({
  default: () => null,
}));
vi.mock("../AddCustomModal", () => ({ default: () => null }));

import AIProviders from "../AIProviders";

const PROVIDERS = ["TypeSafe", "OpenAI", "Anthropic"].map((name) => ({
  provider: name.toLowerCase(),
  display_name: name,
  type: "text",
  has_key: false,
}));

// Moves to another URL on the same page, as a link or the browser would.
function GoTo() {
  const navigate = useNavigate();
  return (
    <>
      <button type="button" onClick={() => navigate("/providers")}>
        Without provider
      </button>
      <button
        type="button"
        onClick={() => navigate("/providers?provider=openai")}
      >
        To OpenAI
      </button>
    </>
  );
}

function renderAt(url) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <HelmetProvider>
      <QueryClientProvider client={client}>
        <ThemeProvider theme={createTheme({ palette: palette("light") })}>
          <MemoryRouter initialEntries={[url]}>
            <AIProviders />
            <GoTo />
          </MemoryRouter>
        </ThemeProvider>
      </QueryClientProvider>
    </HelmetProvider>,
  );
}

const search = () => screen.getByPlaceholderText("Search AI Provider");
const listed = () =>
  screen.queryAllByTestId("provider").map((card) => card.textContent);

beforeEach(() => {
  mocks.get.mockImplementation((url) =>
    Promise.resolve(
      url === "/api-keys/status/"
        ? { data: { result: { providers: PROVIDERS } } }
        : { data: { results: [] } },
    ),
  );
});

describe("AI Providers opened from a provider link", () => {
  it("searches to the provider, and searches again when the link changes", async () => {
    renderAt("/providers?provider=typesafe");
    expect(
      await screen.findByPlaceholderText("Search AI Provider"),
    ).toHaveValue("typesafe");
    await waitFor(() => expect(listed()).toEqual(["TypeSafe"]));
    fireEvent.click(screen.getByRole("button", { name: "To OpenAI" }));
    expect(search()).toHaveValue("openai");
    await waitFor(() => expect(listed()).toEqual(["OpenAI"]));
    fireEvent.click(screen.getByRole("button", { name: "Without provider" }));
    expect(search()).toHaveValue("");
    await waitFor(() => expect(listed()).toHaveLength(PROVIDERS.length));
  });

  it("keeps what the user types, including clearing it", async () => {
    renderAt("/providers?provider=typesafe");
    const field = await screen.findByPlaceholderText("Search AI Provider");
    await waitFor(() => expect(listed()).toEqual(["TypeSafe"]));
    fireEvent.change(field, { target: { value: "" } });
    expect(search()).toHaveValue("");
    await waitFor(() => expect(listed()).toHaveLength(PROVIDERS.length));
    fireEvent.change(search(), { target: { value: "anth" } });
    expect(search()).toHaveValue("anth");
    fireEvent.click(screen.getByRole("button", { name: "To OpenAI" }));
    expect(search()).toHaveValue("openai");
  });
});
