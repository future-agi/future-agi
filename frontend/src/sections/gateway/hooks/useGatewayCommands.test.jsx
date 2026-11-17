/* eslint-disable react/prop-types */
import React, { StrictMode } from "react";
import { cleanup, render, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  rankCommands,
  registeredCommands,
  useCommandStore,
} from "src/sections/common/CommandPalette/commandRegistry";
import { gatewayRailGroups } from "../shell/gateway-rail";
import useGatewayCommands from "./useGatewayCommands";

const commands = () => registeredCommands(useCommandStore.getState().sources);
const destinations = () => commands().map((command) => command.href);
const visiblePaths = (groups) =>
  groups.flatMap((group) => group.items.map((item) => item.path));

const membership = vi.hoisted(() => ({ current: {} }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => membership.current,
}));

beforeEach(() => {
  membership.current = { isReady: false, currentOrganizationId: null, orgLevel: null };
  useCommandStore.setState({ sources: {} });
});
afterEach(cleanup);

describe("R3-SHELL-DX-06 personal key action", () => {
  it.each([1, 5, 8, 10])("opens the existing form for active membership level %s", (orgLevel) => {
    membership.current = { isReady: true, currentOrganizationId: "org-a", orgLevel };
    renderHook(() => useGatewayCommands(gatewayRailGroups()));
    const action = commands().find((command) => command.label === "Create personal API key");
    expect(action).toMatchObject({ group: "Actions", href: "/dashboard/gateway/keys?create=1", requiresLiveRegistration: true });
    for (const query of ["create key", "new token"]) expect(rankCommands(commands(), query)).toContain(action);
    const navigate = vi.fn();
    action.run({ navigate });
    expect(navigate).toHaveBeenCalledExactlyOnceWith("/dashboard/gateway/keys?create=1");
  });

  it.each([
    { isReady: false, currentOrganizationId: "org-a", orgLevel: 10 },
    { isReady: true, currentOrganizationId: null, orgLevel: 10 },
    { isReady: true, currentOrganizationId: "org-a", orgLevel: 0 },
    { isReady: true, currentOrganizationId: "org-a", orgLevel: "10" },
  ])("hides creation without current active membership: %j", (value) => {
    membership.current = value;
    renderHook(() => useGatewayCommands(gatewayRailGroups()));
    expect(commands().some((command) => command.group === "Actions")).toBe(false);
  });

  it("drops captured actions when membership or the permitted destination disappears", () => {
    membership.current = { isReady: true, currentOrganizationId: "org-a", orgLevel: 1 };
    const groups = gatewayRailGroups();
    const { rerender } = renderHook(({ value }) => useGatewayCommands(value), { initialProps: { value: groups } });
    const action = commands().find((command) => command.group === "Actions");
    const navigate = vi.fn();
    membership.current = { isReady: false, currentOrganizationId: "org-b", orgLevel: null };
    rerender({ value: groups });
    expect(commands().some((command) => command.group === "Actions")).toBe(false);
    action.run({ navigate });
    expect(navigate).not.toHaveBeenCalled();
    membership.current = { isReady: true, currentOrganizationId: "org-b", orgLevel: 1 };
    rerender({ value: [] });
    expect(commands()).toEqual([]);
  });

  it("deduplicates the action across two rails and removes it on unmount", () => {
    membership.current = { isReady: true, currentOrganizationId: "org-a", orgLevel: 1 };
    const first = renderHook(() => useGatewayCommands(gatewayRailGroups()));
    const second = renderHook(() => useGatewayCommands(gatewayRailGroups()));
    expect(commands().filter((command) => command.group === "Actions")).toHaveLength(1);
    first.unmount();
    expect(commands().filter((command) => command.group === "Actions")).toHaveLength(1);
    second.unmount();
    expect(commands()).toEqual([]);
  });
});

describe("gateway page commands", () => {
  it.each([
    ["API keys", "API keys"],
    ["logs", "Logs"],
    ["budgets", "Budgets"],
    ["provider", "Providers"],
  ])(
    "ranks %s against the visible destination and navigates to its href",
    (query, title) => {
      const groups = gatewayRailGroups({ integrations: true });
      renderHook(() => useGatewayCommands(groups));
      const item = groups
        .flatMap((group) => group.items)
        .find((entry) => entry.title === title);
      const [match] = rankCommands(commands(), query);
      expect(match).toMatchObject({
        group: "Go to",
        label: `Go to ${title}`,
        href: item.path,
        requiresLiveRegistration: true,
      });
      const navigate = vi.fn();
      // The registry wraps run even for href-only commands; the registered
      // gateway command must actually navigate when the palette invokes it.
      match.run({ navigate });
      expect(navigate).toHaveBeenCalledExactlyOnceWith(item.path);
    },
  );

  it("registers exactly the supplied groups, including permitted extra items", () => {
    const groups = gatewayRailGroups({
      integrations: true,
      extraItems: {
        settings: [
          {
            title: "Billing & credits",
            path: "/dashboard/settings/billing",
            icon: "billing",
          },
        ],
      },
    });
    renderHook(() => useGatewayCommands(groups));
    expect(destinations()).toEqual(visiblePaths(groups));
    expect(commands().every((command) => command.group === "Go to")).toBe(true);
    expect(rankCommands(commands(), "billing")[0].href).toBe(
      "/dashboard/settings/billing",
    );
  });

  it("keeps Models with managed access off without inventing hidden destinations", () => {
    const groups = gatewayRailGroups({
      noManagedModels: true,
      integrations: false,
    });
    renderHook(() => useGatewayCommands(groups));
    expect(destinations()).toEqual(visiblePaths(groups));
    expect(rankCommands(commands(), "Models")[0]).toMatchObject({
      label: "Go to Models",
      href: "/dashboard/gateway/models",
    });
    for (const query of ["Catalog", "Integrations", "Policies"]) {
      expect(rankCommands(commands(), query)).toEqual([]);
    }
  });

  it("removes obsolete commands on group changes while preserving destination IDs", () => {
    const full = gatewayRailGroups({ integrations: true });
    const reduced = gatewayRailGroups({ noManagedModels: true });
    const { rerender, unmount } = renderHook(
      ({ groups }) => useGatewayCommands(groups),
      {
        initialProps: { groups: full },
      },
    );
    const ids = new Map(
      commands().map((command) => [command.href, command.id]),
    );
    rerender({ groups: reduced });
    expect(destinations()).toEqual(visiblePaths(reduced));
    expect(rankCommands(commands(), "integrations")).toEqual([]);
    commands().forEach((command) =>
      expect(command.id).toBe(ids.get(command.href)),
    );
    rerender({ groups: [] });
    expect(commands()).toEqual([]);
    unmount();
    expect(useCommandStore.getState().sources).toEqual({});
  });

  it("deduplicates paths and keeps IDs stable when a label or group changes", () => {
    const item = {
      title: "Logs",
      path: "/dashboard/gateway/logs",
      icon: "logs",
    };
    const groups = [
      { id: "first", subheader: "Observability", items: [item, item] },
      { id: "second", subheader: "Other", items: [item] },
    ];
    const { rerender } = renderHook(({ value }) => useGatewayCommands(value), {
      initialProps: { value: groups },
    });
    expect(commands()).toHaveLength(1);
    const id = commands()[0].id;
    rerender({
      value: [
        {
          id: "renamed",
          subheader: "Requests",
          items: [{ ...item, title: "Request logs" }],
        },
      ],
    });
    expect(commands()).toHaveLength(1);
    expect(commands()[0]).toMatchObject({
      id,
      label: "Go to Request logs",
      href: item.path,
    });
  });

  it.each(["expanded", "mini"])(
    "unmounting the %s rail leaves its peer registered exactly once",
    (removed) => {
      const groups = gatewayRailGroups();
      function Registration() {
        useGatewayCommands(groups);
        return null;
      }
      function Rails({ expanded = true, mini = true }) {
        return (
          <StrictMode>
            {expanded && <Registration key="expanded" />}
            {mini && <Registration key="mini" />}
          </StrictMode>
        );
      }
      const { rerender, unmount } = render(<Rails />);
      expect(destinations()).toEqual(visiblePaths(groups));
      expect(new Set(commands().map((command) => command.id)).size).toBe(
        commands().length,
      );
      rerender(
        <Rails expanded={removed !== "expanded"} mini={removed !== "mini"} />,
      );
      expect(destinations()).toEqual(visiblePaths(groups));
      unmount();
      expect(commands()).toEqual([]);
      expect(useCommandStore.getState().sources).toEqual({});
    },
  );

  it("hands a changed destination to a still-visible peer and removes it when both drop it", () => {
    const full = gatewayRailGroups({ integrations: true });
    const reduced = gatewayRailGroups({ integrations: false });
    const first = renderHook(({ groups }) => useGatewayCommands(groups), {
      initialProps: { groups: full },
    });
    const second = renderHook(({ groups }) => useGatewayCommands(groups), {
      initialProps: { groups: full },
    });
    first.rerender({ groups: reduced });
    expect(rankCommands(commands(), "integrations")).toHaveLength(1);
    second.rerender({ groups: reduced });
    expect(rankCommands(commands(), "integrations")).toEqual([]);
    expect(new Set(destinations())).toEqual(new Set(visiblePaths(reduced)));
    first.unmount();
    expect(destinations()).toEqual(visiblePaths(reduced));
    second.unmount();
    expect(commands()).toEqual([]);
  });
});
