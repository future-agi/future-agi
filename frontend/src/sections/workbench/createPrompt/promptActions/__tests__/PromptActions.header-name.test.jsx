import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { PromptWorkbenchContext } from "../../WorkbenchContext";
import PromptActions from "../PromptActions";

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: { organization_role: "Owner" },
    role: "Owner",
  }),
}));
vi.mock("../../hooks/use-prompt-versions", () => ({
  usePromptVersions: () => ({
    versions: [{ id: "v1", template_version: "V1", is_draft: true }],
    refetch: vi.fn(),
  }),
}));

const workbenchFor = (promptName) => ({
  currentTab: "Playground",
  setCurrentTab: vi.fn(),
  modelConfig: [{ model: "gpt-4o" }],
  prompts: [
    { prompts: [{ role: "user", content: [{ type: "text", text: "Hi" }] }] },
  ],
  results: [{ output: [] }],
  selectedVersions: [
    {
      version: "V1",
      isDraft: true,
      id: "a",
      lastSaved: "2026-11-10T16:54:00Z",
    },
  ],
  isEmptyPrompt: [],
  variableData: {},
  promptName,
  promptGeneratingStatus: [false],
  getStreamingIds: () => [],
});

function renderHeader(promptName) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <PromptWorkbenchContext.Provider value={workbenchFor(promptName)}>
        <MemoryRouter initialEntries={["/dashboard/workbench/create/prompt-1"]}>
          <Routes>
            <Route
              path="/dashboard/workbench/create/:id"
              element={<PromptActions />}
            />
          </Routes>
        </MemoryRouter>
      </PromptWorkbenchContext.Provider>
    </QueryClientProvider>,
  );
  return screen.getByRole("heading", { level: 1 });
}

// Make the browser report that the heading's text is wider than its box,
// which is what happens when the header has less room than the name.
function markCut(el) {
  Object.defineProperty(el, "scrollWidth", { configurable: true, value: 240 });
  Object.defineProperty(el, "clientWidth", { configurable: true, value: 64 });
}

// Every stylesheet rule that targets one of the element's own classes, with
// the media condition it sits under ("" for none) and its flex-wrap value.
function flexWrapRulesFor(el) {
  const classes = [...el.classList];
  const found = [];
  const visit = (rules, media) => {
    for (const rule of Array.from(rules ?? [])) {
      if (!rule.selectorText && rule.cssRules) {
        visit(rule.cssRules, rule.media?.mediaText ?? "nested");
        continue;
      }
      const selectors = String(rule.selectorText ?? "")
        .split(",")
        .map((s) => s.trim());
      if (!classes.some((c) => selectors.includes(`.${c}`))) continue;
      const flexWrap = rule.style?.getPropertyValue?.("flex-wrap") ?? "";
      if (flexWrap) found.push({ media, flexWrap });
    }
  };
  for (const sheet of Array.from(document.styleSheets)) {
    let rules;
    try {
      rules = sheet.cssRules;
    } catch {
      continue;
    }
    visit(rules, "");
  }
  return found;
}

// The prompt editor's header lost the prompt's name when the row had too
// little room ("All Prompts > + V1"), and a name cut by the ellipsis gave no
// way to read it.
describe("Prompt editor header keeps the prompt's name readable", () => {
  it("never forces the header onto one row at any width, so the name is not squeezed to nothing", () => {
    const name = renderHeader("walk-capital");
    const commit = screen.getByRole("button", { name: "Commit" });
    let row = name.parentElement;
    while (row && !row.contains(commit)) row = row.parentElement;

    const rules = flexWrapRulesFor(row);
    // The header row's own wrap rule is visible to this check...
    expect(rules).toContainEqual({ media: "", flexWrap: "wrap" });
    // ...and no width (media query) turns the wrap off.
    expect(rules.filter((r) => r.flexWrap === "nowrap")).toEqual([]);
  });

  it("keeps the whole name in the heading and lets the ellipsis do the cutting", () => {
    const longName = "Customer support triage prompt for refunds v2";
    const name = renderHeader(longName);
    expect(name.textContent).toBe(longName);
    expect(getComputedStyle(name).textOverflow).toBe("ellipsis");
  });

  it("shows the full name on hover when a short name is cut by the width", async () => {
    const name = renderHeader("walk-capital");
    markCut(name);

    fireEvent.mouseEnter(name);

    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "walk-capital",
    );
  });

  it("shows the full name on hover when a long name is cut", async () => {
    const longName = "Customer support triage prompt for refunds v2";
    const name = renderHeader(longName);
    markCut(name);

    fireEvent.mouseEnter(name);

    expect(await screen.findByRole("tooltip")).toHaveTextContent(longName);
  });

  it("control: a name that fits shows no tooltip", () => {
    const name = renderHeader("walk-capital");

    fireEvent.mouseEnter(name);

    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });
});
