import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";

const enqueueSnackbar = vi.fn();
const navigate = vi.fn();

vi.mock("notistack", () => ({
  enqueueSnackbar: (...a) => enqueueSnackbar(...a),
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

const { default: PrebuiltEnvironmentsBrowse } = await import(
  "../PrebuiltEnvironmentsBrowse"
);

const renderBrowse = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <PrebuiltEnvironmentsBrowse />
    </QueryClientProvider>,
  );
};

const tile = (name) => screen.getByRole("button", { name: new RegExp(name) });

// jsdom applies no viewport media queries (MUI emits even the xs value as
// one), so read the element's own Emotion rules and keep the last value
// declared at the given viewport width. Pixel proof is owed in cloud.
const declaredAt = (element, width, property) => {
  let value = "";
  const visit = (rules) => {
    for (const rule of rules) {
      if (rule.media) {
        const bounds = [
          ...rule.media.mediaText.matchAll(/(min|max)-width:\s*([\d.]+)px/g),
        ];
        if (
          bounds.length &&
          bounds.every(([, bound, px]) =>
            bound === "min" ? width >= Number(px) : width <= Number(px),
          )
        )
          visit(rule.cssRules);
      } else if (rule.selectorText) {
        let matches = false;
        try {
          matches = element.matches(rule.selectorText);
        } catch (error) {
          // Vendor pseudo-elements jsdom cannot parse never style this grid.
          if (error.name !== "SyntaxError") throw error;
        }
        const declared = matches && rule.style.getPropertyValue(property);
        if (declared) value = declared.replace(/\s+/g, "");
      }
    }
  };
  for (const sheet of document.styleSheets) visit(sheet.cssRules);
  return value;
};

const columnsAt = (grid, width) =>
  declaredAt(grid, width, "grid-template-columns");

describe("PrebuiltEnvironmentsBrowse card grid fits the page", () => {
  it("shows two cards per row at 1024 so no card runs past the right edge", async () => {
    renderBrowse();
    await screen.findByText("Customer Support Line");

    const grid = tile("Customer Support Line").parentElement;
    expect(columnsAt(grid, 1024)).toBe("repeat(2,minmax(0,1fr))");
  });

  it("keeps three cards per row from 1280 up, each able to shrink", async () => {
    renderBrowse();
    await screen.findByText("Customer Support Line");

    const grid = tile("Customer Support Line").parentElement;
    expect(columnsAt(grid, 1280)).toBe("repeat(3,minmax(0,1fr))");
    expect(columnsAt(grid, 1440)).toBe("repeat(3,minmax(0,1fr))");
    expect(columnsAt(grid, 0)).toBe("minmax(0,1fr)");
  });

  it("lets cards shrink beside the build panel once a template is picked", async () => {
    const user = userEvent.setup();
    renderBrowse();
    await screen.findByText("Customer Support Line");

    const grid = tile("Customer Support Line").parentElement;
    await user.click(tile("Customer Support Line"));

    expect(
      screen.getByRole("tab", { name: /Preview here/ }),
    ).toBeInTheDocument();
    expect(columnsAt(grid, 1440)).toBe("repeat(2,minmax(0,1fr))");
    expect(columnsAt(grid, 0)).toBe("minmax(0,1fr)");
  });
});

describe("PrebuiltEnvironmentsBrowse", () => {
  beforeEach(() => {
    enqueueSnackbar.mockReset();
    navigate.mockReset();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders agent-type category headers and tiles from the fixture", async () => {
    renderBrowse();

    expect(
      await screen.findByText("Customer Support Line"),
    ).toBeInTheDocument();
    ["Voice & chat", "Computer use", "Code"].forEach((group) => {
      expect(screen.getByText(group)).toBeInTheDocument();
    });
    ["Retail Banking Assistant", "Coding", "Verilog"].forEach((name) =>
      expect(screen.getByText(name)).toBeInTheDocument(),
    );
    // "Browser" is both a group header and a tile's surface label (sentence case).
    expect(screen.getAllByText("Browser")).toHaveLength(2);
  });

  it("reports the exact scenario count on a tile", async () => {
    renderBrowse();
    // Customer Support Line: 8 tools, 6 rules, 4 data-trap tables, Starter depth 3.
    expect(await screen.findByText(/68 scenarios/)).toBeInTheDocument();
  });

  it("filters tiles by name as the user searches", async () => {
    const user = userEvent.setup();
    renderBrowse();
    await screen.findByText("Customer Support Line");

    await user.type(
      screen.getByPlaceholderText("Search templates…"),
      "Verilog",
    );

    await waitFor(() =>
      expect(
        screen.queryByText("Customer Support Line"),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByText("Verilog")).toBeInTheDocument();
    expect(screen.queryByText("Voice & chat")).not.toBeInTheDocument();
    expect(screen.getByText("Code")).toBeInTheDocument();
  });

  it("hides the build panel until a template is selected", async () => {
    renderBrowse();
    await screen.findByText("Customer Support Line");

    expect(
      screen.queryByRole("tab", { name: /Preview here/ }),
    ).not.toBeInTheDocument();
  });

  it("opens the inline build panel for the clicked row without navigating", async () => {
    const user = userEvent.setup();
    renderBrowse();
    await screen.findByText("Customer Support Line");

    await user.click(tile("Customer Support Line"));

    expect(
      screen.getByRole("tab", { name: /Preview here/ }),
    ).toBeInTheDocument();
    expect(screen.getByText("Preview a template draft")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Open local draft/ }),
    ).toBeEnabled();
    expect(navigate).not.toHaveBeenCalled();
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("selects a row from the keyboard (Enter)", async () => {
    renderBrowse();
    await screen.findByText("Customer Support Line");

    const target = tile("Customer Support Line");
    expect(target).toHaveAttribute("tabindex", "0");
    fireEvent.keyDown(target, { key: "Enter" });

    expect(
      await screen.findByRole("tab", { name: /Preview here/ }),
    ).toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalled();
  });

  it("collapses the selection when search filters it out", async () => {
    const user = userEvent.setup();
    renderBrowse();
    await screen.findByText("Customer Support Line");

    await user.click(tile("Customer Support Line"));
    expect(
      screen.getByRole("tab", { name: /Preview here/ }),
    ).toBeInTheDocument();

    await user.type(
      screen.getByPlaceholderText("Search templates…"),
      "Verilog",
    );

    await waitFor(() =>
      expect(
        screen.queryByRole("tab", { name: /Preview here/ }),
      ).not.toBeInTheDocument(),
    );
  });

  it("returns to the environments home from the back button", async () => {
    const user = userEvent.setup();
    renderBrowse();
    await screen.findByText("Customer Support Line");

    await user.click(
      screen.getByRole("button", { name: /Back to how you want to start/ }),
    );

    expect(navigate).toHaveBeenCalledWith("/dashboard/simulate/environments");
  });
});
