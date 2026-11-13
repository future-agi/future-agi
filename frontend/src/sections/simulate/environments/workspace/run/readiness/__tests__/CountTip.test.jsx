import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

let cases;
vi.mock("src/api/simulate-environments/readiness", () => ({
  MAX_CASE_KEYS: 200,
  useReadinessCases: (envId, { enabled }) => ({
    data:
      enabled && !cases.isError
        ? cases.names.map((name, at) => ({ key: `k${at}`, name }))
        : undefined,
    isError: enabled && cases.isError,
  }),
}));

const { default: CountTip } = await import("../CountTip");

const tip = (count) => (
  <CountTip envId="env-1" picks={{}} bucket="blocked" count={count}>
    {`${count} blocked`}
  </CountTip>
);

beforeEach(() => {
  cases = { names: [], isError: false };
});

describe("CountTip", () => {
  it("lists the first names on hover and counts the rest", async () => {
    cases.names = Array.from({ length: 12 }, (_, at) => `Scenario ${at + 1}`);
    render(tip(14));
    const count = screen.getByText("14 blocked");
    expect(count).toHaveAttribute("tabindex", "0");
    await userEvent.hover(count);
    expect(await screen.findByText("Scenario 1")).toBeVisible();
    expect(screen.getByText("and 4 more")).toBeVisible();
  });

  it("says when the names can't load", async () => {
    cases.isError = true;
    render(tip(4));
    await userEvent.hover(screen.getByText("4 blocked"));
    expect(
      await screen.findByText("Couldn't load these scenarios."),
    ).toBeVisible();
  });
});
