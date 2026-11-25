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
  // eslint-disable-next-line react/prop-types -- a stand-in card; its props are the real component's
  default: ({ data }) => <div data-testid="provider">{data?.display_name}</div>,
}));
vi.mock("src/components/custom-model-dropdown/CloudProviderCard", () => ({
  // eslint-disable-next-line react/prop-types -- a stand-in card; its props are the real component's
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
      <button type="button" onClick={() => navigate(-1)}>
        Back
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

  it("shows the linked provider when Back returns to its link from another category", async () => {
    renderAt("/providers?provider=typesafe");
    await waitFor(() => expect(listed()).toEqual(["TypeSafe"]));
    fireEvent.click(screen.getByRole("button", { name: "Without provider" }));
    await waitFor(() => expect(listed()).toHaveLength(PROVIDERS.length));
    fireEvent.click(screen.getByRole("button", { name: /^Custom model/ }));
    expect(listed()).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(search()).toHaveValue("typesafe");
    await waitFor(() => expect(listed()).toEqual(["TypeSafe"]));
  });

  it("keeps the picked category when a link drops its provider", async () => {
    renderAt("/providers?provider=typesafe");
    await waitFor(() => expect(listed()).toEqual(["TypeSafe"]));
    fireEvent.click(screen.getByRole("button", { name: /^Custom model/ }));
    fireEvent.click(screen.getByRole("button", { name: "Without provider" }));
    // Still on Custom model: its own search, cleared, and no provider cards.
    expect(screen.getByPlaceholderText("Search Custom Model")).toHaveValue("");
    expect(listed()).toEqual([]);
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

describe("AI Providers empty and filter states", () => {
  it("says a search matched nothing and clears it, not that no models are added", async () => {
    renderAt("/providers");
    await waitFor(() => expect(listed()).toHaveLength(PROVIDERS.length));
    fireEvent.change(search(), { target: { value: "zzz" } });
    expect(
      await screen.findByText("No AI providers match “zzz”"),
    ).toBeInTheDocument();
    expect(screen.queryByText("No Models Added")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(search()).toHaveValue("");
    // At once, not after the search's debounce.
    expect(screen.queryByText(/No AI providers match/)).not.toBeInTheDocument();
    await waitFor(() => expect(listed()).toHaveLength(PROVIDERS.length));
  });

  it("names what the picked category searches when nothing matches", async () => {
    renderAt("/providers");
    await waitFor(() => expect(listed()).toHaveLength(PROVIDERS.length));
    fireEvent.click(screen.getByRole("button", { name: /^Custom model/ }));
    fireEvent.change(screen.getByPlaceholderText("Search Custom Model"), {
      target: { value: "zzz" },
    });
    expect(
      await screen.findByText("No custom models match “zzz”"),
    ).toBeInTheDocument();
  });

  it("keeps the first-run state for an account with nothing added", async () => {
    mocks.get.mockImplementation((url) =>
      Promise.resolve(
        url === "/api-keys/status/"
          ? { data: { result: { providers: [] } } }
          : { data: { results: [] } },
      ),
    );
    renderAt("/providers");
    expect(await screen.findByText("No Models Added")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Clear filters" }),
    ).not.toBeInTheDocument();
  });

  it("shows the selected category as neutral, not purple", async () => {
    const theme = createTheme({ palette: palette("light") });
    renderAt("/providers");
    await waitFor(() => expect(listed()).toHaveLength(PROVIDERS.length));
    const all = screen.getByRole("button", { name: /^All \(/ });
    expect(all).toHaveStyle({
      color: theme.palette.text.primary,
      borderColor: theme.palette.text.secondary,
    });
  });
});
