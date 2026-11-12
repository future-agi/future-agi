/* eslint-disable react/prop-types */
// WALK-A: "Every step repeats 'This tour will guide you through the key
// features and functionalities', including 'Add Keys' and 'Invite team
// members'." Each step says what the user does in it, and no generic tour
// line sits above the steps.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderWithRouter, screen } from "src/utils/test-utils";
import GetStartedHeaderView from "../GetStartedHeaderView";
import InitialSetup from "../setup-steps/InitialSetup";
import InitialSetupOptions from "../setup-steps/InitialSetupOptions/InitialSetupOptions";
import { menuesConstant } from "../constant";

const http = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: http,
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: { id: "person-a", organization_role: "Owner" },
  }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  handleOnDocsClicked: vi.fn(),
  Events: {},
  PropertyName: {},
}));
vi.mock("src/components/external-content/ExternalContentFrame", () => ({
  default: () => null,
}));
// The video card's thumbnail (a lazy image) is not under test.
vi.mock("src/components/image", () => ({
  default: ({ alt }) => <img alt={alt} />,
}));
// Each step's own header line is what this test reads.
vi.mock("../setup-steps/InitialSetupOptions/HeaderContent", () => ({
  default: ({ title, description }) => (
    <div data-testid="step-header" data-title={title}>
      {description}
    </div>
  ),
}));
// Heavy step bodies that are not under test.
vi.mock("src/sections/project/NewProject/NewExperiment", () => ({
  default: () => null,
}));
vi.mock("src/sections/project/NewProject/NewObserve", () => ({
  default: () => null,
}));
vi.mock("src/sections/auth/jwt/setup-org", () => ({
  default: () => null,
}));

const TOUR_LINE =
  /This tour will guide you through the key features and functionalities/i;

beforeEach(() => {
  http.get.mockReset();
  http.get.mockResolvedValue({ data: { status: true, result: {} } });
});

const renderWithClient = (ui) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return renderWithRouter(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
    { route: "/dashboard/get-started" },
  );
};

describe("Get started: each step has its own line", () => {
  it("the page heading says what to do on this page, not the generic tour line", () => {
    renderWithRouter(<GetStartedHeaderView />, {
      route: "/dashboard/get-started",
    });

    expect(screen.getByText("Get started")).toBeInTheDocument();
    expect(screen.queryByText(TOUR_LINE)).not.toBeInTheDocument();
    expect(
      screen.getByText(/Set up your workspace\. Do the steps in any order\./),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View docs" })).toHaveAttribute(
      "href",
      "https://docs.futureagi.com",
    );
  });

  it("the step list says what it is for, in plain words", () => {
    renderWithRouter(
      <InitialSetup
        currentLabel="addKeys"
        setCurrentLabel={vi.fn()}
        data={{}}
      />,
      { route: "/dashboard/get-started" },
    );

    expect(screen.getByText("Setup steps")).toBeInTheDocument();
    expect(
      screen.queryByText(
        /Help you guide through the key features and functionalities/,
      ),
    ).not.toBeInTheDocument();
    expect(screen.getByText("0 of 6 done")).toBeInTheDocument();
  });

  // Guard: the step lines were already rewritten after the walked head, so
  // this passes at the base too; it keeps them distinct.
  it("guard: every step's own line is different and none is the tour line", async () => {
    const lines = new Map();
    for (const { label } of menuesConstant.menusList) {
      const view = renderWithClient(
        <InitialSetupOptions currentLabel={label} setCurrentLabel={vi.fn()} />,
      );
      await act(async () => {});
      const header = screen.getByTestId("step-header");
      lines.set(label, header.textContent.trim());
      view.unmount();
    }

    const all = [...lines.values()];
    expect(all).toHaveLength(menuesConstant.menusList.length);
    all.forEach((line) => {
      expect(line.length).toBeGreaterThan(0);
      expect(line).not.toMatch(TOUR_LINE);
    });
    expect(new Set(all).size).toBe(all.length);
  });
});
