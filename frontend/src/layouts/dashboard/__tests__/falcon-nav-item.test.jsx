import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { render, screen } from "src/utils/test-utils";
import { paths } from "src/routes/paths";

import NavVertical from "../nav-vertical";
import NavMini from "../nav-mini";
import NavHorizontal from "../nav-horizontal";
import { withoutFalconAI } from "../useShellNavData";

// The shell's Falcon AI nav entry follows capabilities.features.falcon_ai,
// like the Falcon AI launcher: it is shown only where Falcon AI is allowed.
// The real useNavData runs; only the other nav lists and the shell's chrome
// are stubbed, and every nav section prints the titles and paths it is given.
// The entry is found by its path; since the design system pass (b9c482fe76)
// its label is "Chat".
const FALCON_ENTRY_LABEL = "Chat";
const falconEntries = () =>
  document.querySelectorAll(
    `[data-testid="nav-list"] li[data-path="${paths.dashboard.falconAI}"]`,
  );

const authState = { user: null };
const capability = { allowed: false, isLoading: false, isError: false };

vi.mock("src/auth/hooks", () => ({ useAuthContext: () => authState }));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: null }),
}));
vi.mock("src/hooks/useCapabilities", async (original) => ({
  .../** @type {typeof import("src/hooks/useCapabilities")} */ (
    await original()
  ),
  useFeatureAllowed: () => capability,
  useCapabilities: () => ({
    data: { features: {} },
    isLoading: false,
    isError: false,
  }),
  useFeatureLocked: () => ({ locked: true, isLoading: false, isError: false }),
}));
vi.mock("src/hooks/use-responsive", () => ({ useResponsive: () => true }));
vi.mock("src/components/settings", () => ({
  useSettingsContext: () => ({ themeLayout: "vertical", onUpdate: () => {} }),
}));
vi.mock("src/components/logo", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/scrollbar", () => {
  function Scrollbar({ children }) {
    return <div>{children}</div>;
  }
  Scrollbar.propTypes = { children: PropTypes.node };
  return { default: Scrollbar };
});
vi.mock("src/components/nav-section", () => {
  function NavList({ data }) {
    return (
      <ul data-testid="nav-list">
        {data
          .flatMap((section) => section.items ?? [])
          .map((item) => (
            <li key={`${item.title}${item.path}`} data-path={item.path}>
              {item.title}
            </li>
          ))}
      </ul>
    );
  }
  NavList.propTypes = { data: PropTypes.array };
  return {
    NavSectionVertical: NavList,
    NavSectionMini: NavList,
    NavSectionHorizontal: NavList,
  };
});
vi.mock("../WorkspaceSwitcher/WorkspaceSwitcher", () => ({
  default: () => null,
}));
vi.mock("../../common/header-shadow", () => ({ default: () => null }));
vi.mock("../config-navigation", async (importOriginal) => {
  const actual = /** @type {typeof import("../config-navigation")} */ (
    await importOriginal()
  );
  return {
    ...actual,
    useNavGatewayData: () => [],
    useNavUpgradeData: () => [],
    useNavSettingsData: () => [],
    useNavDashBoardData: () => [],
    useWorkspaceSettingsNav: () => null,
  };
});

const SHELLS = [
  ["sidebar (NavVertical)", () => <NavVertical openNav={false} />],
  ["mini rail (NavMini)", () => <NavMini />],
  ["top bar (NavHorizontal)", () => <NavHorizontal />],
];
const ROLES = ["Owner", "Admin", "Member"];

describe.each(SHELLS)("the %s Falcon AI entry", (_name, Shell) => {
  beforeEach(() => {
    capability.allowed = false;
    capability.isLoading = false;
    capability.isError = false;
    authState.user = { organization_role: "Owner" };
  });

  it.each(ROLES)(
    "is hidden for an %s where falcon_ai is not allowed",
    (role) => {
      authState.user = { organization_role: role };
      render(<Shell />);
      expect(screen.getByText("Datasets")).toBeInTheDocument();
      expect(falconEntries()).toHaveLength(0);
      expect(screen.queryByText(FALCON_ENTRY_LABEL)).toBeNull();
    },
  );

  it("is hidden while capabilities load or after they fail", () => {
    capability.isLoading = true;
    const { unmount } = render(<Shell />);
    expect(screen.getByText("Datasets")).toBeInTheDocument();
    expect(falconEntries()).toHaveLength(0);
    expect(screen.queryByText(FALCON_ENTRY_LABEL)).toBeNull();
    unmount();
    capability.isLoading = false;
    capability.isError = true;
    render(<Shell />);
    expect(falconEntries()).toHaveLength(0);
    expect(screen.queryByText(FALCON_ENTRY_LABEL)).toBeNull();
  });

  it.each(ROLES)(
    "is shown once for an %s where falcon_ai is allowed",
    (role) => {
      authState.user = { organization_role: role };
      capability.allowed = true;
      render(<Shell />);
      const entries = falconEntries();
      expect(entries).toHaveLength(1);
      expect(entries[0]).toHaveTextContent(FALCON_ENTRY_LABEL);
      expect(screen.getAllByText(FALCON_ENTRY_LABEL)).toHaveLength(1);
      expect(screen.getByText("Datasets")).toBeInTheDocument();
    },
  );
});

// Models (the AI Gateway catalog) is in every shell for every role, whether
// or not Falcon AI is allowed: it is not part of the Falcon entry's gate.
const modelsEntries = () =>
  document.querySelectorAll(
    `[data-testid="nav-list"] li[data-path="${paths.dashboard.gateway.models}"]`,
  );

describe.each(SHELLS)("the %s Models entry", (_name, Shell) => {
  beforeEach(() => {
    capability.allowed = false;
    capability.isLoading = false;
    capability.isError = false;
  });

  it.each(
    ROLES.flatMap((role) => [
      [role, false],
      [role, true],
    ]),
  )("is shown once for an %s (falcon_ai allowed: %s)", (role, allowed) => {
    authState.user = { organization_role: role };
    capability.allowed = allowed;
    render(<Shell />);
    const entries = modelsEntries();
    expect(entries).toHaveLength(1);
    expect(entries[0]).toHaveTextContent("Models");
  });
});

describe("withoutFalconAI", () => {
  it("drops only the Falcon AI entry, and a section it leaves empty", () => {
    const build = {
      subheader: "Build",
      items: [{ title: "Dataset", path: "/a" }],
    };
    const mixed = {
      items: [
        { title: "Falcon AI", path: "/dashboard/falcon-ai" },
        { title: "Docs", path: "/b" },
      ],
    };
    const falconOnly = {
      items: [{ title: "Falcon AI", path: "/dashboard/falcon-ai" }],
    };
    const empty = { subheader: "Empty", items: [] };
    const out = withoutFalconAI([falconOnly, build, mixed, empty]);
    expect(out).toHaveLength(3);
    expect(out[0]).toBe(build);
    expect(out[1].items.map((i) => i.title)).toEqual(["Docs"]);
    expect(out[2]).toBe(empty);
  });
});
