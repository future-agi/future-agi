import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter } from "react-router-dom";
import { palette } from "src/theme/palette";
import AddDatasetDrawer from "../AddDatasetDrawer";
import ExistingDatasetModal from "../../AddRowDrawer/ExistingDatasetModal";

// Render both real panes and MUI's Collapse. Only data/telemetry and unrelated
// creation doors are stubbed; this suite cannot issue API or provider requests.
vi.mock("src/utils/axios", () => ({
  default: {},
  endpoints: {},
}));
vi.mock("@tanstack/react-query", () => ({
  useMutation: () => ({
    mutate: () => {
      throw new Error("Unexpected mutation");
    },
    isPending: false,
  }),
  useQuery: () => ({ data: undefined }),
}));
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({ data: undefined }),
  useGetDatasetColumns: () => ({ data: undefined, isLoading: false }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: {},
  PropertyName: {},
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/FromSearchSelectField", () => ({
  FormSearchSelectFieldControl: () => null,
}));
vi.mock("src/sections/develop-detail/Common/FieldSelection", () => ({
  default: () => null,
}));
vi.mock("../UploadFileModal", () => ({ default: () => null }));
vi.mock("../ManuallyCreateDataset", () => ({ default: () => null }));
vi.mock("src/sections/develop-detail/TaskSuite/NewTaskSuiteDialog", () => ({
  default: () => null,
}));
vi.mock("../../importOptions", () => {
  const options = [
    { id: "addFromExistingDataset", title: "Copy rows from another dataset" },
  ];
  return {
    DATASETS_DOCS: "#datasets-guide",
    IMPORT_OPTIONS: options,
    createDoorShown: () => true,
    offeredImportOptions: () => options,
  };
});

beforeEach(() => {
  const noNetwork = () => {
    throw new Error("Network is forbidden in this presentation test");
  };
  vi.stubGlobal("fetch", vi.fn(noNetwork));
  vi.spyOn(XMLHttpRequest.prototype, "open").mockImplementation(noNetwork);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// jsdom does not lay out containers or apply viewport media queries. Inspect
// the actual rendered Emotion classes and their breakpoint declarations,
// not an imitation of the component's sx. Pixel/overflow proof is owed in cloud.
function declaredStylesAt(element, width) {
  const styles = {};
  const priorities = {};
  function visit(rules) {
    for (const rule of rules) {
      if (rule.media) {
        const bounds = [
          ...rule.media.mediaText.matchAll(/(min|max)-width:\s*([\d.]+)px/g),
        ];
        if (
          bounds.length &&
          bounds.every(([, bound, value]) =>
            bound === "min" ? width >= Number(value) : width <= Number(value),
          )
        )
          visit(rule.cssRules);
      } else if (rule.selectorText) {
        for (const selector of rule.selectorText.split(",")) {
          try {
            if (!element.matches(selector)) continue;
          } catch (error) {
            // Emotion includes vendor pseudo-elements that jsdom's selector
            // engine may not support. They cannot style this layout element.
            if (error.name !== "SyntaxError") throw error;
            continue;
          }
          // These layout rules use class-only selectors. A scoped Collapse
          // wrapper override beats MUI's single-class default even when that
          // default was inserted later. This is not a general CSS evaluator.
          const specificity = (selector.match(/\.[\w-]+/g) ?? []).length;
          for (let i = 0; i < rule.style.length; i += 1) {
            const property = rule.style[i];
            if (specificity < (priorities[property] ?? -1)) continue;
            priorities[property] = specificity;
            styles[property] = rule.style.getPropertyValue(property);
          }
        }
      }
    }
  }
  for (const sheet of document.styleSheets) visit(sheet.cssRules);
  return styles;
}

function renderChooser() {
  render(
    <ThemeProvider theme={createTheme({ palette: palette("light") })}>
      <MemoryRouter>
        <AddDatasetDrawer open onClose={vi.fn()} refreshGrid={vi.fn()} />
      </MemoryRouter>
    </ThemeProvider>,
  );
  const paper = screen
    .getByRole("heading", { name: "New dataset" })
    .closest(".MuiDrawer-paper");
  const layout = paper.firstElementChild;
  return { paper, layout, chooser: layout.firstElementChild };
}

function startCopy() {
  const shell = renderChooser();
  fireEvent.click(
    screen.getByRole("button", { name: "Copy rows from another dataset" }),
  );
  const collapse = shell.layout.querySelector(".MuiCollapse-root");
  const wrapper = collapse.querySelector(".MuiCollapse-wrapper");
  const inner = collapse.querySelector(".MuiCollapse-wrapperInner");
  return { ...shell, collapse, wrapper, inner, pane: inner.firstElementChild };
}

async function openCopy() {
  const shell = startCopy();
  await waitFor(() =>
    expect(shell.collapse).toHaveClass("MuiCollapse-entered"),
  );
  return shell;
}

describe("D02 viewport-bounded copy drawer", () => {
  it("ignores unsupported vendor selectors without dropping matching layout rules", () => {
    const element = document.createElement("div");
    element.className = "copy-layout-probe";
    const style = document.createElement("style");
    style.textContent = `
      .copy-layout-probe::-moz-focus-inner { width: 1px; }
      .copy-layout-probe::-moz-placeholder { width: 2px; }
      .copy-layout-probe:-ms-input-placeholder, .copy-layout-probe { width: 550px; }
      @media (min-width: 1200px) { .copy-layout-probe { width: 640px; } }
    `;
    document.head.append(style);
    try {
      expect(declaredStylesAt(element, 1024).width).toBe("550px");
      expect(declaredStylesAt(element, 1280).width).toBe("640px");
    } finally {
      style.remove();
    }
  });

  it("stacks both real panes at narrow widths and leaves actions in the drawer scroll area", async () => {
    const { paper, layout, chooser, collapse, wrapper, inner, pane } =
      await openCopy();
    for (const width of [375, 390, 1024]) {
      expect(declaredStylesAt(paper, width)).toMatchObject({
        "max-width": "100vw",
        "overflow-y": "auto",
      });
      expect(declaredStylesAt(layout, width)).toMatchObject({
        "flex-direction": "column",
        "max-width": "100%",
      });
      expect(declaredStylesAt(chooser, width)).toMatchObject({
        width: "100%",
        "min-width": "0",
      });
      expect(declaredStylesAt(chooser.firstElementChild, width).height).toBe(
        "auto",
      );
      expect(declaredStylesAt(collapse, width)["max-width"]).toBe("100%");
      expect(declaredStylesAt(wrapper, width).width).toBe("100%");
      expect(declaredStylesAt(inner, width).width).toBe("100%");
      expect(declaredStylesAt(pane, width)).toMatchObject({
        "max-width": "100%",
        height: "auto",
      });
    }
    expect(paper).toContainElement(
      within(pane).getByRole("button", { name: "Cancel" }),
    );
    expect(paper).toContainElement(
      within(pane).getByRole("button", { name: "Add" }),
    );
  });

  it("keeps intrinsic horizontal wrapper sizing while opening and after entering at wide widths", async () => {
    const { layout, collapse, wrapper, inner, pane } = startCopy();
    const expectWideLayout = () => {
      for (const width of [1280, 1440]) {
        expect(declaredStylesAt(layout, width)["flex-direction"]).toBe(
          "row-reverse",
        );
        for (const element of [wrapper, inner]) {
          expect(declaredStylesAt(element, width).width).toBe("auto");
          expect(declaredStylesAt(element, width)["max-width"]).toBeUndefined();
        }
        expect(declaredStylesAt(pane, width)).toMatchObject({
          width: "640px",
          "max-width": "none",
          height: "100dvh",
        });
      }
    };
    // jsdom cannot measure clientWidth or prove a smooth animation. It can
    // catch percentage wrapper constraints during MUI's measurement phase.
    expect(collapse).not.toHaveClass("MuiCollapse-entered");
    expectWideLayout();
    await waitFor(() => expect(collapse).toHaveClass("MuiCollapse-entered"));
    expectWideLayout();
  });

  it.each([1024, 1280, 1440])(
    "preserves the default pane used by AddRowDrawer and AddRowScenario at %ipx",
    async (width) => {
      const { container } = render(
        <ThemeProvider theme={createTheme({ palette: palette("light") })}>
          <MemoryRouter>
            <ExistingDatasetModal
              open
              onClose={vi.fn()}
              refreshGrid={vi.fn()}
              datasetId="existing-dataset"
              closeDrawer={vi.fn()}
            />
          </MemoryRouter>
        </ThemeProvider>,
      );
      const collapse = container.querySelector(".MuiCollapse-root");
      await waitFor(() => expect(collapse).toHaveClass("MuiCollapse-entered"));
      const wrapper = collapse.querySelector(".MuiCollapse-wrapper");
      const inner = collapse.querySelector(".MuiCollapse-wrapperInner");
      const pane = inner.firstElementChild;
      expect(declaredStylesAt(pane, width)).toMatchObject({
        width: "550px",
        height: "100vh",
      });
      expect(declaredStylesAt(pane, width)["max-width"]).toBeUndefined();
      for (const element of [wrapper, inner]) {
        expect(declaredStylesAt(element, width).width).toBe("auto");
        expect(declaredStylesAt(element, width)["max-width"]).toBeUndefined();
      }
    },
  );

  it("does not reserve the hidden copy pane's height in the stacked chooser", () => {
    const { layout } = renderChooser();
    const collapse = layout.querySelector(".MuiCollapse-hidden");
    expect(collapse).not.toBeNull();
    expect(declaredStylesAt(collapse, 375).height).toBe("0");
  });
});
