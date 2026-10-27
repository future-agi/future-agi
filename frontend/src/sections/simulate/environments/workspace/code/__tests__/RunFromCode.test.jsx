import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

const copyToClipboard = vi.fn(() => Promise.resolve(true));
vi.mock("src/utils/utils", async (importOriginal) => ({
  ...(await importOriginal()),
  copyToClipboard,
}));

const { default: RunFromCodeButton } = await import("../RunFromCodeButton");

const ENV = { id: "env-1", name: "Refund line" };
const open = async () => {
  const user = userEvent.setup();
  render(
    <MemoryRouter>
      <RunFromCodeButton env={ENV} repeats={3} />
    </MemoryRouter>,
  );
  await user.click(screen.getByRole("button", { name: "Run from code" }));
  return { user, drawer: within(screen.getByRole("presentation")) };
};

describe("Run from code", () => {
  it("opens on the API tab with a real request to start this environment's run", async () => {
    const { drawer } = await open();
    expect(drawer.getByText("Run from code")).toBeInTheDocument();
    expect(drawer.getByRole("button", { name: "API" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    const code = drawer
      .getAllByText((_, el) => el?.tagName === "PRE")
      .map((el) => el.textContent)
      .join("\n");
    expect(code).toContain("/simulate/api/harness-environments/env-1/run/");
    expect(code).toContain('"trials": 3');
    expect(code).toContain("X-Api-Key");
    expect(code).toContain("/simulate/api/harness-jobs/env-1/scenarios/");
  });

  it("lists Vel's other tabs, each flagged UI only, with the env id and repeats filled in", async () => {
    const { user, drawer } = await open();
    for (const name of [
      "CLI",
      "Python",
      "CI",
      "Your agent (MCP)",
      "Train (RL)",
    ])
      expect(drawer.getByRole("button", { name })).toBeInTheDocument();
    await user.click(drawer.getByRole("button", { name: "CLI" }));
    expect(
      drawer.getByText("fai sim run env-1 --repeats 3 --parallel 20"),
    ).toBeInTheDocument();
    expect(drawer.getAllByText("UI only").length).toBeGreaterThan(0);
  });

  it("says where the environment is managed, with the pull command flagged", async () => {
    const { drawer } = await open();
    expect(drawer.getByText("Managed here")).toBeInTheDocument();
    expect(drawer.getByText("fai env pull env-1")).toBeInTheDocument();
  });

  it("copies a snippet", async () => {
    const { user, drawer } = await open();
    await user.click(drawer.getAllByRole("button", { name: "Copy" })[0]);
    expect(copyToClipboard).toHaveBeenCalledWith(
      expect.stringContaining("harness-jobs/env-1/scenarios"),
    );
  });

  it("uses no em dashes", async () => {
    const { user, drawer } = await open();
    for (const name of [
      "API",
      "CLI",
      "Python",
      "CI",
      "Your agent (MCP)",
      "Train (RL)",
    ]) {
      await user.click(drawer.getByRole("button", { name }));
      expect(screen.getByRole("presentation").textContent).not.toMatch(/—/);
    }
  });
});
