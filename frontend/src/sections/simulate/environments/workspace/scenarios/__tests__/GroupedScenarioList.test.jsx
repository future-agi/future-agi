import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";
import GroupedScenarioList from "../GroupedScenarioList";

// jsdom can't hover, so read the rules emotion generated for the element:
// its base rule (suffix "") and its :hover rule (suffix ":hover").
function rules(el, suffix) {
  const css = [...document.querySelectorAll("style")].map((s) => s.textContent).join("\n");
  return [...el.classList]
    .flatMap((cls) => [...css.matchAll(new RegExp(`\\.${cls}${suffix}\\{([^}]*)\\}`, "g"))])
    .map((m) => m[1])
    .join(";");
}

describe("GroupedScenarioList — sticky group header", () => {
  it("keeps a solid background on hover so scrolled rows don't show through", () => {
    render(
      <GroupedScenarioList
        groups={[{ id: "g1", label: "Handle admin inquiries", rows: [] }]}
        env={{ id: "env-1", name: "Env" }}
      />,
    );
    const header = screen.getByText("Handle admin inquiries").closest('[role="button"]');
    // Solid base, so the tint has something opaque to sit on.
    expect(rules(header, "")).toMatch(/background-color/);
    const hover = rules(header, ":hover");
    expect(hover).toMatch(/background-image/);
    expect(hover).not.toMatch(/background-color/);
  });
});
