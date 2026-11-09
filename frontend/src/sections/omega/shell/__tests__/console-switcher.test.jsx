// The product switcher's Console parts (CP P10, P15): the viewer's line, the
// Inbox count on the entry and on the trigger, and the notification opt-in.
import React from "react";
import { MemoryRouter } from "react-router-dom";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ProductSwitcher from "../ProductSwitcher";
import {
  STALE_AFTER_MS,
  reportConsoleInbox,
  resetConsoleInboxForTests,
} from "../console-inbox";
import { NOTIFY_PREFERENCE_KEY, maybeNotifyInbox } from "../console-notify";
import {
  rememberViewerRefusal,
  resetConsoleAccessForTests,
} from "../omega-access";
import { rolloutQueryClient } from "./rollout-fixtures";
import { ShellTestProviders, ensureWebStorage } from "./test-providers";

const levels = vi.hoisted(() => ({ org: 15, ws: 8, workspace: "ws-a" }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: levels.org }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({
    wsLevel: levels.ws,
    currentWorkspaceId: levels.workspace,
  }),
}));

class FakeNotification {
  static permission = "default";
  static shown = [];
  static requestPermission = vi.fn(async () => {
    FakeNotification.permission = FakeNotification.next;
    return FakeNotification.permission;
  });
  constructor(title, options) {
    FakeNotification.shown.push({ title, ...options });
    this.close = vi.fn();
  }
}

const renderSwitcher = (path = "/dashboard/develop", props = {}, rollout) =>
  render(
    <ShellTestProviders
      queryClient={rollout ? rolloutQueryClient(rollout) : undefined}
    >
      <MemoryRouter initialEntries={[path]}>
        <ProductSwitcher {...props} />
      </MemoryRouter>
    </ShellTestProviders>,
  );

const openMenu = async () => {
  await userEvent.click(screen.getByTestId("omega-product-switcher"));
  return screen.getByRole("menu", { name: "Products" });
};

beforeEach(() => {
  ensureWebStorage();
  window.localStorage.removeItem(NOTIFY_PREFERENCE_KEY);
  Object.assign(levels, { org: 15, ws: 8, workspace: "ws-a" });
  FakeNotification.permission = "default";
  FakeNotification.next = "granted";
  FakeNotification.shown = [];
  window.Notification = FakeNotification;
});

afterEach(() => {
  act(() => {
    resetConsoleInboxForTests();
    resetConsoleAccessForTests();
  });
  delete window.Notification;
  vi.useRealTimers();
});

describe("product switcher: viewer (CP P10)", () => {
  it("tells a viewer to ask an admin, and offers no count or notifications", async () => {
    Object.assign(levels, { org: 3, ws: 1 });
    act(() => reportConsoleInbox(4));
    renderSwitcher();
    const menu = await openMenu();
    expect(
      within(menu).getByTestId("omega-product-detail-omega-console"),
    ).toHaveTextContent("Ask an admin for access");
    expect(within(menu).queryByTestId("omega-console-inbox-count")).toBeNull();
    expect(within(menu).queryByTestId("omega-console-notify")).toBeNull();
    expect(screen.queryByTestId("omega-console-inbox-dot")).toBeNull();
  });

  it("shows the same line after the exchange refused a viewer whose levels looked fine", async () => {
    renderSwitcher();
    act(() => rememberViewerRefusal("ws-a"));
    const menu = await openMenu();
    expect(
      within(menu).getByTestId("omega-product-detail-omega-console"),
    ).toHaveTextContent("Ask an admin for access");
  });

  it("keeps the usual line for owners, admins and members", async () => {
    for (const [org, ws] of [
      [15, null],
      [8, 1],
      [3, 3],
    ]) {
      Object.assign(levels, { org, ws });
      const view = renderSwitcher();
      const menu = await openMenu();
      expect(
        within(menu).getByTestId("omega-product-detail-omega-console"),
      ).toHaveTextContent("Hosted agents for your team");
      view.unmount();
    }
  });
});

describe("product switcher: Inbox count (CP P15)", () => {
  it("shows the count on the entry and a dot on the trigger, with text labels", async () => {
    renderSwitcher();
    expect(screen.queryByTestId("omega-console-inbox-dot")).toBeNull();
    act(() => reportConsoleInbox(3));
    expect(screen.getByTestId("omega-console-inbox-dot")).toBeInTheDocument();
    expect(screen.getByTestId("omega-product-switcher")).toHaveAccessibleName(
      "Switch product (current: Future AGI), 3 approvals waiting in Omega Console",
    );
    const menu = await openMenu();
    const count = within(menu).getByTestId("omega-console-inbox-count");
    expect(count).toHaveTextContent("3");
    expect(count).toHaveAccessibleName("3 approvals waiting");
  });

  it("shows 99+ above 99, nothing at 0, and the collapsed trigger the same", () => {
    renderSwitcher("/dashboard/develop", { collapsed: true });
    act(() => reportConsoleInbox(0));
    expect(screen.queryByTestId("omega-console-inbox-dot")).toBeNull();
    act(() => reportConsoleInbox(250));
    expect(screen.getByTestId("omega-console-inbox-dot")).toBeInTheDocument();
  });

  it("drops a count nobody has refreshed for ten minutes", () => {
    vi.useFakeTimers();
    renderSwitcher();
    act(() => reportConsoleInbox(2));
    expect(screen.getByTestId("omega-console-inbox-dot")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(STALE_AFTER_MS - 1));
    expect(screen.getByTestId("omega-console-inbox-dot")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.queryByTestId("omega-console-inbox-dot")).toBeNull();
  });

  it("puts no dot on the trigger on the Console page itself", () => {
    renderSwitcher("/dashboard/console");
    act(() => reportConsoleInbox(5));
    expect(screen.queryByTestId("omega-console-inbox-dot")).toBeNull();
  });
});

describe("product switcher: notifications opt-in (CP P15)", () => {
  it("asks the browser from the click, then notifies only when hidden and rising", async () => {
    renderSwitcher();
    let menu = await openMenu();
    const item = within(menu).getByTestId("omega-console-notify");
    expect(item).toHaveAttribute("data-state", "off");
    expect(maybeNotifyInbox(0, 1, { visibilityState: "hidden" })).toBe(false);

    await userEvent.click(item);
    expect(FakeNotification.requestPermission).toHaveBeenCalledTimes(1);
    expect(window.localStorage.getItem(NOTIFY_PREFERENCE_KEY)).toBe("on");

    const hidden = { visibilityState: "hidden" };
    expect(maybeNotifyInbox(0, 1, { visibilityState: "visible" })).toBe(false);
    expect(maybeNotifyInbox(2, 2, hidden)).toBe(false);
    expect(maybeNotifyInbox(3, 1, hidden)).toBe(false);
    expect(maybeNotifyInbox(1, 2, hidden)).toBe(true);
    expect(FakeNotification.shown).toEqual([
      {
        title: "Omega Console",
        body: "2 approvals waiting in Omega Console",
        tag: "omega-console-inbox",
      },
    ]);

    menu = await openMenu();
    const on = within(menu).getByTestId("omega-console-notify");
    expect(on).toHaveAttribute("data-state", "on");
    await userEvent.click(on);
    expect(window.localStorage.getItem(NOTIFY_PREFERENCE_KEY)).toBeNull();
    expect(maybeNotifyInbox(1, 5, hidden)).toBe(false);
  });

  it("stays off when the browser says no, and says so when blocked", async () => {
    FakeNotification.next = "denied";
    renderSwitcher();
    let menu = await openMenu();
    await userEvent.click(within(menu).getByTestId("omega-console-notify"));
    expect(window.localStorage.getItem(NOTIFY_PREFERENCE_KEY)).toBeNull();
    menu = await openMenu();
    const blocked = within(menu).getByTestId("omega-console-notify");
    expect(blocked).toHaveAttribute("data-state", "blocked");
    expect(blocked).toHaveAttribute("aria-disabled", "true");
  });

  it("offers nothing where the browser has no notifications", async () => {
    delete window.Notification;
    renderSwitcher();
    const menu = await openMenu();
    expect(within(menu).queryByTestId("omega-console-notify")).toBeNull();
  });
});

describe("product switcher: Console not admitted (READINESS 3.13)", () => {
  const notAdmitted = { admitted: ["sandboxes", "deployments", "hub"] };

  // The AI Gateway is always listed (gateway UI v2, merged in round 7), so the
  // switcher stays; the Console's entry, its count and its dot do not.
  it("lists only Future AGI and the AI Gateway on a Future AGI page, with no count, whatever the Console last said", async () => {
    act(() => reportConsoleInbox(3));
    renderSwitcher("/dashboard/develop", {}, notAdmitted);
    const trigger = screen.getByTestId("omega-product-switcher");
    expect(trigger).toHaveAccessibleName("Switch product (current: Future AGI)");
    expect(screen.queryByTestId("omega-console-inbox-dot")).toBeNull();
    const menu = await openMenu();
    expect(within(menu).getAllByRole("menuitem").map((o) => o.textContent)).toEqual([
      expect.stringContaining("Future AGI"),
      expect.stringContaining("AI Gateway"),
    ]);
    expect(
      within(menu).queryByTestId("omega-product-option-omega-console"),
    ).toBeNull();
    expect(within(menu).queryByTestId("omega-console-inbox-count")).toBeNull();
  });

  it("offers no Console entry, count or notification opt-in on a Console page", async () => {
    act(() => reportConsoleInbox(3));
    renderSwitcher("/dashboard/console/agents", {}, notAdmitted);
    const menu = await openMenu();
    expect(
      within(menu).queryByTestId("omega-product-option-omega-console"),
    ).toBeNull();
    expect(within(menu).queryByTestId("omega-console-inbox-count")).toBeNull();
    expect(within(menu).queryByTestId("omega-console-notify")).toBeNull();
  });
});
