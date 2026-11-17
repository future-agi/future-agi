import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "src/utils/test-utils";
import ActionBar from "../ActionBar";

vi.mock("src/components/iconify", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock(
  "src/sections/common/CommandPalette/commandRegistry",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useRegisterCommands: () => {},
  }),
);

afterEach(cleanup);

const action = (overrides = {}) => ({
  id: "evaluate",
  label: "Evaluate",
  inMore: true,
  run: vi.fn(),
  ...overrides,
});
const bar = (actions) => <ActionBar surface="w49-live-menu" actions={actions} />;
const openMore = () =>
  fireEvent.click(screen.getByRole("button", { name: "More actions" }));

it("W49-MENU-READY: updates an open More menu's reason, availability and handler", () => {
  const oldRun = vi.fn();
  const currentRun = vi.fn();
  const { rerender } = render(
    bar([action({ disabled: true, reason: "Add rows first", run: oldRun })]),
  );
  openMore();
  expect(screen.getByRole("menuitem", { name: /Evaluate/ })).toHaveAttribute(
    "aria-disabled",
    "true",
  );

  rerender(bar([action({ disabled: false, run: currentRun })]));

  const item = screen.getByRole("menuitem", { name: "Evaluate" });
  expect(item).not.toHaveAttribute("aria-disabled", "true");
  expect(screen.queryByText("Add rows first")).not.toBeInTheDocument();
  fireEvent.click(item);
  expect(currentRun).toHaveBeenCalledTimes(1);
  expect(oldRun).not.toHaveBeenCalled();
});

it("W49-MENU-REVOKED: an open More menu cannot run an action disabled since opening", () => {
  const run = vi.fn();
  const { rerender } = render(bar([action({ run })]));
  openMore();

  rerender(
    bar([action({ run, disabled: true, reason: "Run access is required" })]),
  );

  const item = screen.getByRole("menuitem", { name: /Evaluate/ });
  expect(item).toHaveAttribute("aria-disabled", "true");
  expect(item).toHaveTextContent("Run access is required");
  fireEvent.click(item);
  expect(run).not.toHaveBeenCalled();
});

it("W49-MENU-REMOVED: removes obsolete actions from an already open More menu", () => {
  const run = vi.fn();
  const download = action({ id: "download", label: "Download…" });
  const { rerender } = render(bar([action({ run }), download]));
  openMore();

  rerender(bar([download]));

  expect(screen.queryByRole("menuitem", { name: "Evaluate" })).toBeNull();
  expect(screen.getByRole("menuitem", { name: "Download…" })).toBeVisible();
  expect(run).not.toHaveBeenCalled();
});

it("W49-MENU-NESTED: resolves an open action submenu from its current action id", () => {
  const oldRun = vi.fn();
  const currentRun = vi.fn();
  const parent = (child) => ({
    id: "run",
    label: "Run",
    priority: 1,
    menu: [child],
  });
  const { rerender } = render(
    bar([parent(action({ run: oldRun, disabled: true, reason: "Add rows first" }))]),
  );
  fireEvent.click(screen.getByRole("button", { name: "Run" }));
  expect(screen.getByRole("menuitem", { name: /Evaluate/ })).toHaveAttribute(
    "aria-disabled",
    "true",
  );

  rerender(
    bar([
      { id: "view", label: "View", priority: 1, run: vi.fn() },
      parent(action({ run: currentRun })),
    ]),
  );

  const item = screen.getByRole("menuitem", { name: "Evaluate" });
  expect(item).not.toHaveAttribute("aria-disabled", "true");
  fireEvent.click(item);
  expect(currentRun).toHaveBeenCalledTimes(1);
  expect(oldRun).not.toHaveBeenCalled();
});
