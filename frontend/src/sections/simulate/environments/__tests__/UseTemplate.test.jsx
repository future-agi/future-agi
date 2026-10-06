import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";

const navigate = vi.fn();
let params = { templateId: "banking_support" };

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate, useParams: () => params };
});

const usePrebuiltEnvironments = vi.fn();
vi.mock("src/api/simulate-environments/prebuilt", () => ({
  usePrebuiltEnvironments: () => usePrebuiltEnvironments(),
  usePrebuiltEnvironment: () => ({ data: undefined }),
}));

const { default: UseTemplate } = await import("../UseTemplate");

const TEMPLATE = {
  id: "banking_support",
  name: "Banking — Card, Fraud & Account Support",
  surface: "voice",
  tagline: "Retail-bank support with step-up auth.",
  scenarioCount: 12,
  tools: [{ name: "lock_card", desc: "x" }],
  rules: ["Never move money"],
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
    params = { templateId: "banking_support" };
    usePrebuiltEnvironments.mockReturnValue({ data: [TEMPLATE], isLoading: false });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the panel for the routed template", () => {
    renderSection();
    expect(
      screen.getByText("Banking — Card, Fraud & Account Support"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Open template/ })).toBeInTheDocument();
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
    expect(screen.queryByRole("button", { name: /Open template/ })).toBeNull();
    expect(screen.getByText(/isn't available/i)).toBeInTheDocument();
  });
});
