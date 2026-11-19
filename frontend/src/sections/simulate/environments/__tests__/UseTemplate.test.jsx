import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";

const navigate = vi.fn();
let params = { templateId: "env-voice-support" };

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate, useParams: () => params };
});

const usePrebuiltEnvironments = vi.fn();
vi.mock("src/api/simulate-environments/prebuilt", () => ({
  usePrebuiltEnvironments: () => usePrebuiltEnvironments(),
}));

const { default: UseTemplate } = await import("../UseTemplate");

const TEMPLATE = {
  id: "env-voice-support",
  name: "Customer Support Line",
  surface: "voice",
  tagline: "Inbound phone support for an online storefront",
  difficulty: "Starter",
  seed: { tables: [{ name: "orders", rows: 500, note: "delayed" }] },
  tools: [{ name: "lookup_order", desc: "x" }],
  rules: ["Refunds need approval"],
  evalPreset: ["task_success"],
};

const renderSection = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <UseTemplate />
    </QueryClientProvider>,
  );
};

describe("UseTemplate", () => {
  beforeEach(() => {
    navigate.mockReset();
    params = { templateId: "env-voice-support" };
    usePrebuiltEnvironments.mockReturnValue({ data: [TEMPLATE], isLoading: false });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the build panel for the routed template", () => {
    renderSection();
    expect(screen.getByText("Customer Support Line")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Build here/ })).toBeInTheDocument();
  });

  it("goes back to the templates browse from the back button", async () => {
    const user = userEvent.setup();
    renderSection();

    await user.click(screen.getByRole("button", { name: /Back to templates/ }));

    expect(navigate).toHaveBeenCalledWith(
      "/dashboard/simulate/environments/templates",
    );
  });

  it("shows a graceful not-found for an unknown template id", () => {
    params = { templateId: "does-not-exist" };
    renderSection();
    expect(screen.queryByRole("tab", { name: /Build here/ })).not.toBeInTheDocument();
    expect(screen.getByText(/isn't available/i)).toBeInTheDocument();
  });
});
