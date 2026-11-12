import React from "react";
import PropTypes from "prop-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "src/utils/test-utils";
import ObserveHeader from "../ObserveHeader";

// At a 1024 px window the Observe project header row has 792 px. It keeps
// every control inside that width: the row wraps, the left group and the
// project picker shrink, and the right-hand controls wrap.
// jsdom has no layout, so these tests read the flex rules that keep the row
// inside its width, and the structure they apply to.
const h = vi.hoisted(() => ({
  observeId: "project-1",
  pathname: "/dashboard/observe/project-1/llm-tracing",
  projectName:
    "support-agent-production-replica-project-with-a-name-longer-than-the-row",
}));

vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => vi.fn(),
  useParams: () => ({ observeId: h.observeId }),
  useLocation: () => ({ pathname: h.pathname }),
}));
vi.mock("src/routes/hooks/use-url-state", async (importOriginal) => ({
  ...(await importOriginal()),
  useUrlState: (_key, initial) => [initial, vi.fn()],
}));
vi.mock("src/hooks/use-local-storage", async (importOriginal) => ({
  ...(await importOriginal()),
  getStorage: () => false,
  setStorage: vi.fn(),
}));
vi.mock("src/api/project/project-detail", async (importOriginal) => ({
  ...(await importOriginal()),
  useGetProjectDetails: () => ({ data: null }),
}));
vi.mock("../LLMTracing/common", async (importOriginal) => ({
  ...(await importOriginal()),
  useProjectList: () => ({
    data: [{ id: h.observeId, name: h.projectName }],
    isLoading: false,
  }),
}));
// Icons render as named stubs, so no glyph needs registering and no
// "Missing offline icon" label joins a button's name.
vi.mock("src/components/iconify", async (importOriginal) => ({
  ...(await importOriginal()),
  default: ({ icon }) => <span data-testid={icon} />,
}));
vi.mock("src/components/tooltip/CustomTooltip", async (importOriginal) => {
  const MockTooltip = ({ children }) => children;
  MockTooltip.propTypes = { children: PropTypes.node };
  return { ...(await importOriginal()), default: MockTooltip };
});
vi.mock("src/components/share-dialog", async (importOriginal) => ({
  ...(await importOriginal()),
  ShareDialog: () => null,
}));
vi.mock("src/sections/project/TagEditor", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => <span data-testid="tag-editor" />,
}));
vi.mock("../../project-detail/ConfigureProject", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));

const ZERO = /^0(px)?$/;

// Read through getPropertyValue and compare with toBe: a value cssstyle does
// not keep reads "" and fails, where toHaveStyle could pass vacuously.
const css = (element, property) =>
  window.getComputedStyle(element).getPropertyValue(property);

let client;

const renderHeader = (text = "LLM Tracing") =>
  render(
    <QueryClientProvider client={client}>
      <ObserveHeader text={text} refreshData={vi.fn()} />
    </QueryClientProvider>,
  );

// The tooltips are pass-through here, as the real one adds no element.
const headerParts = () => {
  const back = screen.getByRole("button", { name: "Back" });
  const left = back.parentElement;
  const row = left.parentElement;
  const autoRefresh = screen.getByRole("switch");
  const right = autoRefresh.parentElement;
  const share = screen.getByTestId("solar:share-linear").closest("button");
  const icons = share.parentElement;
  return { back, left, row, autoRefresh, right, share, icons };
};

const showLastUpdated = () =>
  act(() => {
    window.dispatchEvent(
      new CustomEvent("observe-aggregation-completed", {
        detail: {
          observeId: h.observeId,
          queryCompletedAt: "2026-11-11T11:55:00",
        },
      }),
    );
  });

beforeEach(() => {
  h.observeId = "project-1";
  h.pathname = "/dashboard/observe/project-1/llm-tracing";
  client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
});

afterEach(() => {
  client.clear();
});

describe("Observe project header at 1024", () => {
  it("lets the header row wrap instead of running past the window edge", () => {
    renderHeader();
    const { row, left, right } = headerParts();

    // Left group and right-hand controls are the row's two flex items.
    expect(left.parentElement).toBe(row);
    expect(right.parentElement).toBe(row);
    expect(css(row, "flex-wrap")).toBe("wrap");
    expect(css(row, "min-width")).toMatch(ZERO);
  });

  it("lets the left group shrink and caps the project picker, whose name ends in an ellipsis", () => {
    renderHeader();
    const { left } = headerParts();

    expect(css(left, "min-width")).toMatch(ZERO);

    const picker = screen.getByRole("button", { name: h.projectName });
    expect(picker.parentElement).toBe(left);
    // 73 (Back) + 240 + 28 (tag icon) + gaps leaves the right-hand 348 px
    // room on the same line in 792.
    expect(css(picker, "max-width")).toBe("240px");
    expect(css(picker, "min-width")).toBe("200px");

    const name = screen.getByText(h.projectName);
    expect(css(name, "text-overflow")).toBe("ellipsis");
    expect(css(name, "white-space")).toBe("nowrap");
  });

  it("gives the tag editor its own box that shrinks with the row and caps no tags", () => {
    renderHeader();
    const { left } = headerParts();

    const slot = screen.getByTestId("tag-editor").parentElement;
    expect(slot).not.toBe(left);
    expect(slot.parentElement).toBe(left);
    expect(slot.children).toHaveLength(1);
    expect(css(slot, "flex-shrink")).toBe("1");
    expect(css(slot, "min-width")).toMatch(ZERO);
    // No fixed cap: a row with room shows every tag.
    expect(["", "none"]).toContain(css(slot, "max-width"));
  });

  it.each([
    ["LLM Tracing", "/dashboard/observe/project-1/llm-tracing", 5],
    ["Sessions", "/dashboard/observe/project-1/sessions", 5],
    ["Insights", "/dashboard/observe/project-1/insights", 4],
  ])(
    "keeps every right-hand control whole and wrapping inside the window on %s",
    (text, pathname, iconButtons) => {
      h.pathname = pathname;
      renderHeader(text);
      showLastUpdated();
      const { right, autoRefresh, icons } = headerParts();

      expect(css(right, "flex-wrap")).toBe("wrap");
      expect(css(right, "justify-content")).toBe("flex-end");
      expect(css(right, "min-width")).toMatch(ZERO);

      const stamp = screen.getByText(/Last updated on/).parentElement;
      expect(stamp.parentElement).toBe(right);
      expect(css(stamp, "flex-shrink")).toBe("0");
      expect(css(autoRefresh, "flex-shrink")).toBe("0");
      expect(icons.parentElement).toBe(right);
      expect(css(icons, "flex-shrink")).toBe("0");

      // Reload, (export), Docs, Settings and Share all stay in the row.
      expect(icons.querySelectorAll("button")).toHaveLength(iconButtons);
      for (const icon of [
        "solar:refresh-linear",
        "solar:document-text-linear",
        "solar:settings-linear",
        "solar:share-linear",
      ]) {
        expect(icons).toContainElement(screen.getByTestId(icon));
      }
    },
  );
});
