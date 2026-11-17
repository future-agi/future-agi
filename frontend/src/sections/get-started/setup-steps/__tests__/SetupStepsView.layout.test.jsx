/* eslint-disable react/prop-types */
// WALK-A: "At 1440x900 the 'Create Dataset' button on 'Create your first
// dataset' is cut off at the bottom edge of the step box." The page is a
// height-limited column (the dashboard's main area), so the step box was
// squeezed down to its 480px minimum and its right side hid what overflowed.
// The step box keeps its full height (the page scrolls instead), and nothing
// between it and a step's button hides overflow. jsdom does no layout: this
// reads the styles that decide it.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderWithRouter, screen } from "src/utils/test-utils";
import SetupStepsView from "../SetupStepsView";

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

beforeEach(() => {
  http.get.mockReset();
  http.get.mockResolvedValue({ data: { status: true, result: {} } });
});

const renderSteps = async (currentLabel) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = renderWithRouter(
    <QueryClientProvider client={client}>
      <SetupStepsView
        data={{}}
        currentLabel={currentLabel}
        setCurrentLabel={vi.fn()}
        setShowDemoModal={vi.fn()}
      />
    </QueryClientProvider>,
    { route: `/dashboard/get-started?tab=${currentLabel}` },
  );
  await act(async () => {});
  return view;
};

const hidesVerticalOverflow = (element) => {
  const style = window.getComputedStyle(element);
  return style.overflowY === "hidden" || style.overflow === "hidden";
};

describe("Get started: the step box never cuts off a step's button", () => {
  it("the step box keeps its full height inside the page column", async () => {
    const { container } = await renderSteps("createFirstDataset");
    const stepBox = container.firstElementChild;

    expect(window.getComputedStyle(stepBox).display).toBe("flex");
    expect(window.getComputedStyle(stepBox).flexShrink).toBe("0");
  });

  it("the step content scrolls rather than hiding what does not fit", async () => {
    const { container } = await renderSteps("createFirstDataset");
    const stepContent = container.firstElementChild.lastElementChild;

    expect(window.getComputedStyle(stepContent).overflowY).toBe("auto");
  });

  it("nothing between the step box and 'Create Dataset' hides it", async () => {
    const { container } = await renderSteps("createFirstDataset");
    const stepBox = container.firstElementChild;
    const button = screen.getByRole("button", { name: "Go to Datasets" });

    const clipping = [];
    for (let el = button.parentElement; el; el = el.parentElement) {
      if (hidesVerticalOverflow(el)) clipping.push(el);
      if (el === stepBox) break;
    }
    expect(clipping).toEqual([]);
  });
});
