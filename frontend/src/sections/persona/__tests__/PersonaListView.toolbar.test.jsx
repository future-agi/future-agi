import React from "react";
import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "src/utils/test-utils";

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));

vi.mock("src/api/persona/persona", () => ({
  useGetPersonasPaginated: () => ({
    data: { results: [], count: 0 },
    isLoading: false,
    isFetching: false,
  }),
  useBulkDeletePersonas: () => ({ mutate: vi.fn(), isPending: false }),
}));

const { default: PersonaListView } = await import("../PersonaListView");

// The create/edit and info drawers inside the list use React Query.
const renderList = (ui) =>
  render(
    <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>,
  );

// jsdom lays nothing out, so read the element's own Emotion rules (last
// declaration wins) at a viewport width. Pixel proof is owed in cloud.
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
          // Vendor pseudo-elements jsdom cannot parse never style these.
          if (error.name !== "SyntaxError") throw error;
        }
        const declared = matches && rule.style.getPropertyValue(property);
        if (declared) value = declared.replace(/\s+/g, " ").trim();
      }
    }
  };
  for (const sheet of document.styleSheets) visit(sheet.cssRules);
  return value;
};

describe("PersonaListView toolbar fits at 1024", () => {
  const renderWithDocs = () =>
    renderList(<PersonaListView toolbarActions={<button>View Docs</button>} />);
  const searchBox = () => {
    const input = screen.getByPlaceholderText("Search personas");
    return input.closest(".MuiFormControl-root");
  };

  it("wraps the toolbar onto a second line instead of squeezing its controls", () => {
    renderWithDocs();

    const toolbar = searchBox().parentElement;
    expect(toolbar).toContainElement(
      screen.getByRole("button", { name: "Create persona" }),
    );
    expect(declaredAt(toolbar, 1024, "flex-wrap")).toBe("wrap");
    // A flex gap, not sibling margins, so a wrapped line starts flush left.
    expect(declaredAt(toolbar, 1024, "gap")).not.toBe("");
  });

  it("keeps View Docs and Create persona together at the right, never shrunk", () => {
    renderWithDocs();

    const create = screen.getByRole("button", { name: "Create persona" });
    const actions = create.parentElement;
    expect(actions).toContainElement(
      screen.getByRole("button", { name: "View Docs" }),
    );
    expect(actions).not.toContainElement(searchBox());
    expect(declaredAt(actions, 1024, "margin-left")).toBe("auto");
    expect(declaredAt(actions, 1024, "flex-shrink")).toBe("0");
  });

  it("lets the search box give up width before the row wraps, up to 280px", () => {
    renderWithDocs();

    expect(declaredAt(searchBox(), 1024, "width")).toBe("");
    expect(declaredAt(searchBox(), 1024, "max-width")).toBe("280px");
  });
});

describe("PersonaListView toolbar actions", () => {
  it("renders the given actions just before Create persona", () => {
    renderList(<PersonaListView toolbarActions={<button>View Docs</button>} />);

    const docs = screen.getByRole("button", { name: "View Docs" });
    const create = screen.getByRole("button", { name: "Create persona" });
    expect(docs.parentElement).toBe(create.parentElement);
    expect(docs.nextElementSibling).toBe(create);
  });

  it("renders only Create persona when no actions are given", () => {
    renderList(<PersonaListView />);

    expect(
      screen.getByRole("button", { name: "Create persona" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "View Docs" })).toBeNull();
  });
});
