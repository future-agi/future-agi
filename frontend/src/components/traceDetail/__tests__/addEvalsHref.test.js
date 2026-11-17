import { describe, expect, it } from "vitest";
import { addEvalsHrefFor } from "../addEvalsHref";

describe("addEvalsHrefFor (options-truth L3 G10)", () => {
  it("opens the task creator on the span's project with a span filter draft", () => {
    const href = addEvalsHrefFor({
      projectId: "p-42",
      span: { id: "span-7", trace: "t-1" },
      returnTo: "/dashboard/develop/x?trace=t-1",
    });
    const url = new URL(href, "http://127.0.0.1");
    expect(url.pathname).toBe("/dashboard/tasks/create");
    expect(url.searchParams.get("project")).toBe("p-42");
    expect(url.searchParams.get("returnTo")).toBe(
      "/dashboard/develop/x?trace=t-1",
    );
    const draft = JSON.parse(
      window.localStorage.getItem(
        `task-draft-${url.searchParams.get("draft")}`,
      ),
    );
    expect(draft.values.project).toBe("p-42");
    expect(draft.values.rowType).toBe("spans");
    expect(draft.values.filters).toHaveLength(1);
    expect(draft.values.filters[0].filterConfig.filterValue).toBe("span-7");
  });
  it("is null without a project or a span", () => {
    expect(addEvalsHrefFor({ projectId: null, span: { id: "s" } })).toBeNull();
    expect(addEvalsHrefFor({ projectId: "p", span: null })).toBeNull();
  });
});
