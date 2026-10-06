import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";

const navigate = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

vi.mock("src/api/simulate-environments/prebuilt", () => ({
  usePrebuiltEnvironment: () => ({
    data: {
      scenarios: [
        {
          scenario_key: "lost-card",
          name: "Caller reports a lost card",
          use_case: "card_services",
          persona: { name: "Priya Raman" },
        },
      ],
    },
  }),
}));

const { default: TemplateBuildPanel } = await import("../TemplateBuildPanel");

const TEMPLATE = {
  id: "banking_support",
  environmentId: "template-env-1",
  name: "Banking — Card, Fraud & Account Support",
  surface: "voice",
  tagline: "Inbound card and fraud support",
  scenarioCount: 12,
  tools: [
    { name: "verify_identity", desc: "x" },
    { name: "lock_card", desc: "x" },
    { name: "report_fraud", desc: "x" },
  ],
  rules: ["Never move money", "Step-up before sensitive actions"],
  evalPreset: ["task_success", "tone"],
};

const renderPanel = (props = {}) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <TemplateBuildPanel template={TEMPLATE} {...props} />
    </QueryClientProvider>,
  );
};

describe("TemplateBuildPanel", () => {
  beforeEach(() => {
    navigate.mockReset();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("states what the template holds from the template itself", () => {
    renderPanel();
    expect(screen.getByText("3 the world answers")).toBeInTheDocument();
    expect(screen.getByText("2 graded on every run")).toBeInTheDocument();
    expect(screen.getByText("12 ready to run")).toBeInTheDocument();
    expect(screen.getByText("2 selected")).toBeInTheDocument();
  });

  it("lists the scenarios the harness generated for it", () => {
    renderPanel();
    expect(screen.getByText("Caller reports a lost card")).toBeInTheDocument();
    expect(screen.getByText("card_services · Priya Raman")).toBeInTheDocument();
  });

  it("opens the shared template itself rather than copying it", async () => {
    const user = userEvent.setup();
    renderPanel();

    await user.click(screen.getByRole("button", { name: /Open template/ }));

    expect(navigate).toHaveBeenCalledWith(
      "/dashboard/simulate/environments/template-env-1",
    );
  });
});
