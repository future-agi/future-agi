// @vitest-environment jsdom
/* eslint-disable react/prop-types */
// W9 A3 (W9-SCOPE §5): collapsed groups are remembered per grouping in
// layout prefs (a full load reads them back), apart from the column layout.
import React from "react";
import { beforeEach, describe, expect, it } from "vitest";
import { act, render } from "@testing-library/react";
import useGroupCollapse, {
  GROUPS_SURFACE,
  collapsedOf,
  toggleCollapsed,
} from "../useGroupCollapse";
import {
  LAYOUT_VERSION,
  layoutKey,
  readLayout,
  useLayoutStore,
} from "src/sections/common/ux/oneview/layoutPrefs";

const KEY = layoutKey(null, GROUPS_SURFACE);
let api;
function Host({ by }) {
  const [collapsed, toggle] = useGroupCollapse(by);
  api = { collapsed, toggle };
  return null;
}

beforeEach(() => {
  window.localStorage.clear();
  useLayoutStore.setState({ byKey: {} });
});

describe("pure", () => {
  it("toggles one key of one grouping; others stay", () => {
    const base = { v: LAYOUT_VERSION, extras: { pinned: ["x"] } };
    const one = toggleCollapsed(base, "owner", "Ravi Menon");
    expect(collapsedOf(one, "owner")).toEqual(["Ravi Menon"]);
    expect(collapsedOf(one, "tag")).toEqual([]);
    expect(one.extras.pinned).toEqual(["x"]);
    expect(
      collapsedOf(toggleCollapsed(one, "owner", "Ravi Menon"), "owner"),
    ).toEqual([]);
  });
});

describe("useGroupCollapse", () => {
  it("collapses, stores, and reads back after a reload", () => {
    const { unmount } = render(<Host by="owner" />);
    expect(api.collapsed).toEqual([]);
    act(() => api.toggle("Ravi Menon"));
    expect(api.collapsed).toEqual(["Ravi Menon"]);
    expect(collapsedOf(readLayout(KEY), "owner")).toEqual(["Ravi Menon"]);
    unmount();
    // A full load: the in-memory store is empty, storage has it.
    useLayoutStore.setState({ byKey: {} });
    render(<Host by="owner" />);
    expect(api.collapsed).toEqual(["Ravi Menon"]);
  });

  it("another grouping has its own state", () => {
    render(<Host by="tag" />);
    act(() => api.toggle("golden"));
    expect(collapsedOf(readLayout(KEY), "tag")).toEqual(["golden"]);
    expect(collapsedOf(readLayout(KEY), "owner")).toEqual([]);
  });
});
