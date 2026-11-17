import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  ThemeProvider,
  createTheme,
  decomposeColor,
  getContrastRatio,
  recomposeColor,
} from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { customShadows } from "src/theme/custom-shadows";
import { dsShape } from "src/theme/ds-tokens";
import { componentsOverrides } from "src/theme/overrides";
import { endpoints } from "src/utils/axios";

import MCPManagementSection from "./MCPManagementSection";

const { statusQuery, configQuery, patchMutate, org, snackbar, api, connect } =
  vi.hoisted(() => ({
    statusQuery: { current: {} },
    configQuery: { current: {} },
    patchMutate: vi.fn(),
    org: { level: 8 },
    snackbar: vi.fn(),
    api: { get: vi.fn(), post: vi.fn() },
    connect: { real: false },
  }));

// Turning MCP on or off writes the org's gateway config: Owner (15) or
// Admin (8) only, read by the real useGatewayAdmin from the org level.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: org.level }),
}));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { get: api.get, post: api.post },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: snackbar }));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "gw-1",
    isLoading: false,
    // Connect's setup snippets need the gateway's address.
    ...(connect.real
      ? { gateway: { baseUrl: "https://org-gateway.example.test/v1" } }
      : {}),
  }),
}));
vi.mock("../providers/hooks/useGatewayConfig", async (importOriginal) => ({
  ...(await importOriginal()),
  useReloadConfig: () => ({ mutate: vi.fn(), isPending: false }),
  useGatewayConfig: () => configQuery.current,
}));
vi.mock("./hooks/useMCPConfig", () => ({
  useMCPStatus: () => statusQuery.current,
}));
vi.mock("./hooks/useMCPConfigPatch", () => ({
  useMCPConfigPatch: () => ({ mutate: patchMutate, isPending: false }),
}));

// This file tests the page shell; each tab's own tests cover its content.
const { stubTab } = vi.hoisted(() => ({
  stubTab: (name) => () => ({ default: () => <div>{`${name} tab`}</div> }),
}));
vi.mock("./calls/SiemExportDrawer", () => ({ default: () => null }));
vi.mock("./overview/OverviewTab", stubTab("overview"));
vi.mock("./servers/ServersTab", stubTab("servers"));
vi.mock("./virtual/VirtualServersTab", stubTab("virtual"));
vi.mock("./calls/ToolCallsTab", stubTab("calls"));
vi.mock("./credentials/CredentialsTab", stubTab("credentials"));
vi.mock("./clients/MCPClientsTab", stubTab("clients"));
vi.mock("./guardrails/GuardrailsTab", stubTab("guardrails"));
vi.mock("./playground/PlaygroundTab", stubTab("playground"));
vi.mock("./resources/ResourcesTab", stubTab("resources"));
vi.mock("./prompts/PromptsTab", stubTab("prompts"));
vi.mock("./registry/RegistryTab", stubTab("registry"));
// One test renders Connect's own content, to count every line on the page.
vi.mock("./connect/ConnectTab", async (importOriginal) => {
  const { default: ConnectTab } = await importOriginal();
  const { default: Stub } = stubTab("connect")();
  return {
    default: (props) => (connect.real ? <ConnectTab {...props} /> : <Stub />),
  };
});
vi.mock("./trust/TrustTab", stubTab("trust"));
vi.mock("./approvals/ApprovalsTab", stubTab("approvals"));

const TITLE = "Turn off MCP?";
const BODY =
  "Agents and MCP clients lose their tools until MCP is turned on again. Servers, credentials and virtual servers are kept.";
const REFUSED = "The gateway refused the new config.";
const BANNER_TITLE = "MCP is off for this organization";
// Under the banner, on every tab, while MCP is off for the organization.
const ORG_OFF_LINE =
  "What you add here is saved and connects when you turn MCP on.";
// Why Try a tool is disabled while MCP is off for the organization.
const TRY_REASON = "Turn on MCP to test";
// The hover hint on Connect; the banner says MCP is off.
const ORG_OFF_HINT = "Turn on MCP to connect servers and clients.";
const CONNECT_NOTE = "These snippets do not turn MCP on.";
const DEPLOYMENT_LINE = "Not available in this deployment";
const TAB_LABELS = [
  "Overview",
  "Servers",
  "Virtual servers",
  "Tool calls",
  "Credentials",
  "MCP clients",
  "Guardrails",
  "Try a tool",
  "Resources",
  "Prompts",
  "Connect",
  "Tool revisions",
  "Approvals",
];

const ON = {
  catalog: true,
  credential_vault: true,
  virtual_servers: true,
  client_oauth: true,
  client_installs: true,
  tool_call_explorer: true,
};
const SAVED = {
  enabled: true,
  servers: { "crm-tools": { url: "https://crm.example/mcp" } },
};

function setOn() {
  statusQuery.current = {
    data: { enabled: true, features: ON },
    isError: false,
  };
  configQuery.current = { data: { mcp: SAVED } };
}

function setOrgOff(features = ON) {
  statusQuery.current = { data: { enabled: false, features }, isError: false };
  configQuery.current = { data: { mcp: { ...SAVED, enabled: false } } };
}

// The console's theme, built as theme/index.jsx builds it: its component
// overrides decide the colour of every tab and make tab rows scrollable by
// default.
function appTheme() {
  const theme = createTheme({
    palette: palette("light"),
    typography,
    customShadows: customShadows("light"),
    shape: dsShape,
  });
  theme.components = componentsOverrides(theme);
  return theme;
}
const theme = appTheme();

let location;
function LocationProbe() {
  location = useLocation();
  return null;
}

function page(url = "/dashboard/gateway/mcp") {
  return (
    <HelmetProvider>
      <ThemeProvider theme={theme}>
        <MemoryRouter initialEntries={[url]}>
          <Routes>
            <Route
              path="/dashboard/gateway/mcp/:tab?"
              element={<MCPManagementSection />}
            />
          </Routes>
          <LocationProbe />
        </MemoryRouter>
      </ThemeProvider>
    </HelmetProvider>
  );
}

function mount(url) {
  return render(page(url));
}

function openTurnOff() {
  fireEvent.click(screen.getByRole("button", { name: "Turn off MCP" }));
  return screen.getByRole("dialog", { name: TITLE });
}

// Selector weight: ids, then classes, attributes and pseudo-classes (the
// argument of :not counts, :not itself does not).
function specificity(selector) {
  const ids = (selector.match(/#[\w-]+/g) || []).length;
  const classes =
    (selector.match(/\.[\w-]+/g) || []).length +
    (selector.match(/\[[^\]]*\]/g) || []).length +
    (selector.match(/:(?!not\()[\w-]+/g) || []).length;
  return ids * 1000 + classes;
}

// The value of `property` that wins on `el` across the page's style rules:
// the heaviest matching selector, and the later rule on a tie. jsdom lays
// nothing out and does not rank selectors, so the cascade is computed here.
// With `hover`, rules for the hovered state count too.
function cascaded(el, property, { hover = false } = {}) {
  let best = null;
  for (const sheet of Array.from(document.styleSheets)) {
    for (const rule of Array.from(sheet.cssRules)) {
      const value = rule.selectorText && rule.style.getPropertyValue(property);
      if (!value) continue;
      for (const selector of rule.selectorText.split(",")) {
        if (selector.includes(":hover") && !hover) continue;
        let matches = false;
        try {
          matches = el.matches(selector.replaceAll(":hover", ""));
        } catch {
          matches = false;
        }
        const weight = specificity(selector);
        if (matches && (!best || weight >= best.weight))
          best = { weight, value: value.trim() };
      }
    }
  }
  return best?.value;
}

// One spelling per colour: a style rule keeps the value as written
// ("#A1A1AA"), the theme may spell it another way. A value that is not a
// colour (or no value) comes back as it is, so a miss reads as a mismatch.
function normalizedColor(value) {
  if (value === undefined) return value;
  try {
    return recomposeColor(decomposeColor(value));
  } catch {
    return value;
  }
}

function tabColor(tab, options) {
  return normalizedColor(cascaded(tab, "color", options));
}

// The colour text in `el` shows: its own, else the nearest one it inherits.
function shownColor(el) {
  for (let node = el; node; node = node.parentElement) {
    const value = cascaded(node, "color");
    if (value) return normalizedColor(value);
  }
  return undefined;
}

// Whether `el` or an element around it is clipped to 1 px, the visually
// hidden pattern: a screen reader reads it, nobody sees it.
function visuallyHidden(el) {
  for (let node = el; node; node = node.parentElement) {
    const width = cascaded(node, "width");
    if (width === "1px" && cascaded(node, "height") === "1px") return true;
  }
  return false;
}

// The elements an e2e flow's page.getByText(phrase) finds: any case,
// whitespace collapsed, the phrase inside a longer text too, hidden elements
// included, and the innermost element when a child holds the whole phrase.
function elementsSaying(phrase) {
  const want = phrase.toLowerCase();
  const says = (el) =>
    !["SCRIPT", "STYLE", "NOSCRIPT"].includes(el.tagName) &&
    el.textContent.replace(/\s+/g, " ").toLowerCase().includes(want);
  return Array.from(document.body.querySelectorAll("*")).filter(
    (el) => says(el) && !Array.from(el.children).some(says),
  );
}

// The page says MCP is off in exactly one element: the banner's title.
function expectSaidOnce() {
  const title = screen.getByText(BANNER_TITLE);
  const said = elementsSaying(BANNER_TITLE);
  expect(said.map((el) => el.textContent)).toEqual([BANNER_TITLE]);
  expect(said[0]).toBe(title);
  return title;
}

beforeEach(() => {
  vi.clearAllMocks();
  org.level = 8;
  connect.real = false;
  setOn();
  api.post.mockReset().mockResolvedValue({
    data: { result: { version: 2, gateway_synced: true, warnings: [] } },
  });
});

describe("Turn off MCP", () => {
  it.each([
    ["an Owner", 15],
    ["an Admin", 8],
  ])("is offered to %s while MCP is on", (_, level) => {
    org.level = level;
    mount();
    expect(screen.getByText("MCP on")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Turn off MCP" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Turn on MCP" })).toBeNull();
  });

  it.each([
    ["a Member", 3],
    ["a Viewer", 1],
    ["someone whose role has not loaded", null],
  ])("is not shown to %s", (_, level) => {
    org.level = level;
    mount();
    expect(screen.getByText("MCP on")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Turn off MCP" })).toBeNull();
  });

  it.each([
    ["off for the organization", () => setOrgOff()],
    [
      "off on this gateway",
      () => {
        statusQuery.current = {
          data: {
            enabled: false,
            reason: "gateway_mcp_disabled",
            features: ON,
          },
          isError: false,
        };
      },
    ],
    [
      "unknown because the status read failed",
      () => {
        statusQuery.current = { data: undefined, isError: true };
      },
    ],
  ])("is not shown while MCP is %s", (_, arrange) => {
    arrange();
    mount();
    expect(screen.queryByRole("button", { name: "Turn off MCP" })).toBeNull();
  });

  // The saved switch is on in both, but MCP does not read as on.
  it.each([
    [
      "Gateway unreachable",
      { enabled: false, reason: "gateway_unreachable", features: ON },
    ],
    ["Not reported", { enabled: false, features: ON }],
  ])("is not shown beside %s", (label, data) => {
    statusQuery.current = { data, isError: false };
    mount();
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Turn off MCP" })).toBeNull();
  });

  it("is offered while MCP is on with no server yet", () => {
    configQuery.current = { data: { mcp: { enabled: true } } };
    mount();
    expect(screen.getByText("No servers yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Turn off MCP" })).toBeEnabled();
  });

  // The gateway's answer wins over a stale saved switch: the chip reads MCP
  // on, so turning it off still means something.
  it("is offered while the gateway reports MCP on over a saved switch that is off", () => {
    statusQuery.current = {
      data: { enabled: true, features: ON },
      isError: false,
    };
    configQuery.current = { data: { mcp: { ...SAVED, enabled: false } } };
    mount();
    expect(screen.getByText("MCP on")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Turn off MCP" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Turn on MCP" })).toBeNull();
  });

  it("asks first, in these words, and Cancel changes nothing", () => {
    mount();
    const dialog = openTurnOff();
    expect(dialog).toHaveAccessibleDescription(BODY);
    expect(within(dialog).getByText(BODY)).toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "Cancel" }),
    ).toBeEnabled();
    expect(
      within(dialog).getByRole("button", { name: "Turn off" }),
    ).toBeEnabled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(patchMutate).not.toHaveBeenCalled();
  });

  it("saves through the same write as Turn on MCP, with the off value", () => {
    setOrgOff();
    const { unmount } = mount();
    fireEvent.click(screen.getByRole("button", { name: "Turn on MCP" }));
    unmount();

    setOn();
    mount();
    const dialog = openTurnOff();
    fireEvent.click(within(dialog).getByRole("button", { name: "Turn off" }));
    expect(patchMutate.mock.calls.map(([patch]) => patch)).toEqual([
      { enabled: true },
      { enabled: false },
    ]);
    expect(patchMutate.mock.calls[1][1]).toEqual(
      expect.objectContaining({
        onSuccess: expect.any(Function),
        onError: expect.any(Function),
      }),
    );

    act(() => patchMutate.mock.calls[1][1].onSuccess({ gateway_synced: true }));
    expect(snackbar).toHaveBeenCalledWith(
      "MCP turned off for this organization",
      {
        variant: "success",
      },
    );
  });

  it("sends both values to the organization's update-config endpoint", async () => {
    const { useMCPConfigPatch } = await vi.importActual(
      "./hooks/useMCPConfigPatch",
    );
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    // eslint-disable-next-line react/prop-types
    const wrapper = ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => useMCPConfigPatch("gw-1"), {
      wrapper,
    });
    await act(() => result.current.mutateAsync({ enabled: true }));
    await act(() => result.current.mutateAsync({ enabled: false }));
    expect(api.post.mock.calls).toEqual([
      [endpoints.gateway.updateConfig("gw-1"), { mcp: { enabled: true } }],
      [endpoints.gateway.updateConfig("gw-1"), { mcp: { enabled: false } }],
    ]);
  });

  it("keeps MCP on and shows the API's message when the save fails", () => {
    mount();
    const dialog = openTurnOff();
    fireEvent.click(within(dialog).getByRole("button", { name: "Turn off" }));
    expect(patchMutate).toHaveBeenCalledTimes(1);

    act(() =>
      patchMutate.mock.calls[0][1].onError({
        response: {
          status: 400,
          data: { message: "The gateway refused the new config." },
        },
      }),
    );
    expect(
      within(screen.getByRole("dialog", { name: TITLE })).getByRole("alert"),
    ).toHaveTextContent("The gateway refused the new config.");
    expect(screen.getByText("MCP on")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Turn off MCP", hidden: true }),
    ).toBeInTheDocument();
    expect(snackbar).not.toHaveBeenCalledWith(
      "MCP turned off for this organization",
      expect.anything(),
    );
    // Said once, in the open question.
    expect(snackbar).not.toHaveBeenCalledWith(REFUSED, expect.anything());
    expect(patchMutate).toHaveBeenCalledTimes(1);
  });

  it("shows a failed save as an error toast once the question has closed", async () => {
    const { rerender } = mount();
    const dialog = openTurnOff();
    fireEvent.click(within(dialog).getByRole("button", { name: "Turn off" }));
    expect(patchMutate).toHaveBeenCalledTimes(1);

    // A status read fails while the save is pending, so the question closes.
    statusQuery.current = { data: undefined, isError: true };
    rerender(page());
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: TITLE })).toBeNull(),
    );

    act(() =>
      patchMutate.mock.calls[0][1].onError({
        response: { status: 400, data: { message: REFUSED } },
      }),
    );
    expect(snackbar).toHaveBeenCalledWith(REFUSED, { variant: "error" });
    expect(screen.queryByRole("dialog", { name: TITLE })).toBeNull();
    expect(snackbar).not.toHaveBeenCalledWith(
      "MCP turned off for this organization",
      expect.anything(),
    );
  });

  it("closes the question when MCP stops reading as on, and does not ask again by itself", async () => {
    const { rerender } = mount();
    openTurnOff();

    // A status read fails while the question is open.
    statusQuery.current = { data: undefined, isError: true };
    rerender(page());
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: TITLE })).toBeNull(),
    );
    expect(screen.queryByRole("button", { name: "Turn off MCP" })).toBeNull();

    // The next read answers again: the action is back, the question is not.
    setOn();
    rerender(page());
    expect(screen.getByRole("button", { name: "Turn off MCP" })).toBeEnabled();
    expect(screen.queryByRole("dialog", { name: TITLE })).toBeNull();
    expect(patchMutate).not.toHaveBeenCalled();
  });
});

describe("tabs while MCP is off for the organization", () => {
  it("names why each off tab is off; Try a tool stays disabled and Connect still opens", () => {
    setOrgOff({ ...ON, tool_call_explorer: false });
    mount();
    // Try a tool is disabled, not hidden, and the Tab key still reaches it.
    const tryTab = screen.getByRole("tab", { name: "Try a tool" });
    expect(tryTab).toHaveAttribute("aria-disabled", "true");
    expect(tryTab).toHaveAttribute("tabindex", "0");
    expect(tryTab).not.toBeDisabled();
    expect(tryTab).toHaveAccessibleDescription(TRY_REASON);
    const connect = screen.getByRole("tab", { name: "Connect" });
    expect(connect).toHaveAccessibleDescription(BANNER_TITLE);
    expect(connect).not.toHaveAttribute("aria-disabled");
    expect(connect).not.toBeDisabled();
    // A switch that is off for this build keeps its own line.
    expect(
      screen.getByRole("tab", { name: "Tool calls" }),
    ).toHaveAccessibleDescription(DEPLOYMENT_LINE);
    // Resources and Prompts open whether MCP is on or off for the
    // organization.
    for (const name of ["Resources", "Prompts"])
      expect(screen.getByRole("tab", { name })).not.toBeDisabled();
    for (const name of ["Servers", "MCP clients", "Guardrails"])
      expect(screen.getByRole("tab", { name })).not.toHaveAccessibleDescription(
        BANNER_TITLE,
      );

    // Try a tool does not open: the gateway serves no tools until MCP is on.
    fireEvent.click(tryTab);
    expect(location.search).toBe("");
    expect(screen.getByText("overview tab")).toBeInTheDocument();
    expect(screen.queryByText("playground tab")).toBeNull();

    // Connect opens. The banner already says MCP is off, so the page adds no
    // off line above it.
    fireEvent.click(connect);
    expect(location.search).toBe("?tab=connect");
    expect(screen.getByText("connect tab")).toBeInTheDocument();
    expect(screen.queryByTestId("mcp-tab-off-notice")).toBeNull();
    expect(
      screen.getByRole("tab", { name: "Connect", selected: true }),
    ).toHaveAccessibleDescription(BANNER_TITLE);
  });

  it("says why Try a tool is disabled in text on the page, without a hover", () => {
    setOrgOff();
    mount("/dashboard/gateway/mcp?tab=servers");
    expect(screen.getByText("servers tab")).toBeInTheDocument();
    expect(screen.queryByRole("tooltip")).toBeNull();
    const tryTab = screen.getByRole("tab", { name: "Try a tool" });
    const reason = document.getElementById(
      tryTab.getAttribute("aria-describedby"),
    );
    expect(reason?.textContent).toBe(TRY_REASON);
    expect(visuallyHidden(reason)).toBe(false);
    expect(reason).toBeVisible();
    expect(screen.getAllByText(TRY_REASON)).toHaveLength(1);
    // The page's text colour: 4.5:1 or better on the page, in both themes.
    expect(shownColor(reason)).toBe(
      normalizedColor(theme.palette.text.secondary),
    );
    for (const mode of ["light", "dark"]) {
      const { text, background } = palette(mode);
      for (const surface of [background.paper, background.default])
        expect([
          mode,
          getContrastRatio(text.secondary, surface) >= 4.5,
        ]).toEqual([mode, true]);
    }
  });

  it("opens a Try a tool link with its reason as the page's line, said once", () => {
    setOrgOff();
    mount("/dashboard/gateway/mcp?tab=playground");
    const tab = screen.getByRole("tab", { name: "Try a tool", selected: true });
    expect(tab).toHaveAttribute("aria-disabled", "true");
    const line = screen.getByTestId("mcp-tab-off-notice");
    expect(line).toHaveTextContent(TRY_REASON);
    const target = tab.getAttribute("aria-describedby");
    expect(document.getElementById(target)).toBe(line);
    expect(visuallyHidden(line)).toBe(false);
    expect(screen.getAllByText(TRY_REASON)).toHaveLength(1);
    expect(screen.queryByText("playground tab")).toBeNull();
    expectSaidOnce();
    expect(screen.getByText(ORG_OFF_LINE)).toBeInTheDocument();
  });

  it.each([
    ["Overview", "overview"],
    ["Servers", "servers"],
  ])("says MCP is off once on %s: in the banner", (_, slug) => {
    setOrgOff();
    mount(`/dashboard/gateway/mcp?tab=${slug}`);
    expect(screen.getByText(`${slug} tab`)).toBeInTheDocument();
    const title = expectSaidOnce();
    // The line under it says what adding does, and only that.
    const line = screen.getByText(ORG_OFF_LINE);
    expect(
      title.compareDocumentPosition(line) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(screen.queryByTestId("mcp-tab-off-notice")).toBeNull();
  });

  it("says MCP is off once on Connect, its setup snippets included", () => {
    setOrgOff();
    configQuery.current = {
      data: {
        mcp: {
          ...SAVED,
          enabled: false,
          virtual_servers: { review: { title: "Review" } },
        },
      },
    };
    connect.real = true;
    mount("/dashboard/gateway/mcp?tab=connect");
    expect(screen.getByText("Connect a client")).toBeInTheDocument();
    // Connect keeps its own, different sentence.
    expect(screen.getByText(CONNECT_NOTE)).toBeInTheDocument();
    expect(screen.getByText(ORG_OFF_LINE)).toBeInTheDocument();
    expectSaidOnce();
    expect(screen.queryByTestId("mcp-tab-off-notice")).toBeNull();
  });

  it("describes Connect with the banner's own sentence", () => {
    setOrgOff();
    mount();
    const title = expectSaidOnce();
    const tab = screen.getByRole("tab", { name: "Connect" });
    expect(tab).toHaveAccessibleDescription(BANNER_TITLE);
    const target = tab.getAttribute("aria-describedby");
    expect(document.getElementById(target)).toBe(title);
  });

  it("shows Connect's hint on hover only, so it never stays over the open tab", async () => {
    setOrgOff({ ...ON, tool_call_explorer: false });
    mount();
    const tab = screen.getByRole("tab", { name: "Connect" });
    // A click focuses the tab: focus alone shows nothing.
    fireEvent.focus(tab);
    expect(screen.queryByRole("tooltip")).toBeNull();

    fireEvent.mouseOver(tab);
    expect(
      within(await screen.findByRole("tooltip")).getByText(ORG_OFF_HINT),
    ).toBeInTheDocument();
    // The hint does not repeat the banner's sentence.
    expectSaidOnce();
    fireEvent.click(tab);
    expect(screen.getByText("connect tab")).toBeInTheDocument();
    fireEvent.mouseLeave(tab);
    await waitFor(() => expect(screen.queryByRole("tooltip")).toBeNull());

    // A tab that cannot open keeps its tooltip on keyboard focus.
    for (const [name, reason] of [
      ["Try a tool", TRY_REASON],
      ["Tool calls", DEPLOYMENT_LINE],
    ]) {
      const off = screen.getByRole("tab", { name });
      fireEvent.focus(off);
      expect(
        within(await screen.findByRole("tooltip")).getByText(reason),
      ).toBeInTheDocument();
      fireEvent.blur(off);
      await waitFor(() => expect(screen.queryByRole("tooltip")).toBeNull());
    }
  });

  it("puts no off line on Try a tool or Connect while MCP is on", () => {
    mount();
    for (const name of ["Try a tool", "Connect"]) {
      const tab = screen.getByRole("tab", { name });
      expect(tab).not.toHaveAccessibleDescription(BANNER_TITLE);
      expect(tab).not.toHaveAttribute("aria-disabled");
    }
    expect(screen.queryByText(BANNER_TITLE)).toBeNull();
    expect(screen.queryByText(ORG_OFF_HINT)).toBeNull();
    expect(screen.queryByText(ORG_OFF_LINE)).toBeNull();
    expect(screen.queryByText(TRY_REASON)).toBeNull();
  });

  it("shows every tab that cannot open dimmed but readable, at rest and on hover", () => {
    setOrgOff({ ...ON, tool_call_explorer: false });
    mount();
    const dimmed = normalizedColor(theme.palette.text.disabled);
    for (const name of ["Tool calls", "Try a tool"]) {
      const tab = screen.getByRole("tab", { name });
      expect([name, tabColor(tab)]).toEqual([name, dimmed]);
      expect([name, tabColor(tab, { hover: true })]).toEqual([name, dimmed]);
      // Not faded a second time: the label stays readable.
      expect([name, cascaded(tab, "opacity")]).toEqual([name, "1"]);
    }
    // Resources and Prompts open, so they are not dimmed.
    for (const name of ["Servers", "Resources", "Prompts"]) {
      const tab = screen.getByRole("tab", { name });
      expect([name, tabColor(tab)]).not.toEqual([name, dimmed]);
    }
  });

  it("shows Connect, which waits for MCP, dimmer than the rest, at 4.5:1 or better, at rest and on hover", () => {
    setOrgOff();
    mount();
    // Connect still opens, so its label keeps text contrast: tertiary, not
    // the disabled colour.
    const muted = normalizedColor(theme.palette.text.tertiary);
    for (const mode of ["light", "dark"]) {
      const { text, background } = palette(mode);
      expect([
        mode,
        getContrastRatio(text.tertiary, background.paper) >= 4.5,
      ]).toEqual([mode, true]);
    }
    const regular = tabColor(screen.getByRole("tab", { name: "Servers" }));
    expect(regular).not.toBe(muted);
    expect(regular).not.toBe(normalizedColor(theme.palette.text.disabled));
    const tab = screen.getByRole("tab", { name: "Connect" });
    expect(tabColor(tab)).toBe(muted);
    expect(tabColor(tab, { hover: true })).toBe(muted);
    expect(cascaded(tab, "opacity")).toBe("1");
  });

  it("shows Connect as the selected tab once it is open", () => {
    setOrgOff();
    mount("/dashboard/gateway/mcp?tab=connect");
    const tab = screen.getByRole("tab", { name: "Connect", selected: true });
    expect(tabColor(tab)).toBe(normalizedColor(theme.palette.text.primary));
    expect(tab).toHaveAccessibleDescription(BANNER_TITLE);
  });
});

// Every gateway tab row (CustomTabs) is styled once, in the theme: the open
// tab is marked by MUI's sliding underline in the selected-border colour.
function underline() {
  return screen
    .getByRole("tablist")
    .closest(".MuiTabs-root")
    .querySelector(".MuiTabs-scroller > .MuiTabs-indicator");
}

describe("the tab row's underline", () => {
  it("is the console's sliding underline, as on every other gateway tab row", () => {
    mount("/dashboard/gateway/mcp?tab=servers");
    const line = underline();
    expect(line).not.toBeNull();
    expect(line.style.display).not.toBe("none");
    expect(normalizedColor(cascaded(line, "background-color"))).toBe(
      normalizedColor(
        theme.palette.border?.selected || theme.palette.secondary.main,
      ),
    );
    // The open tab draws no underline of its own.
    const open = screen.getByRole("tab", { name: "Servers", selected: true });
    expect(cascaded(open, "box-shadow")).toBeUndefined();
  });

  describe("on a row that wraps", () => {
    // jsdom lays nothing out: the row stands as two lines of 40 px tabs, and
    // the open tab sits on the first.
    let spies = [];
    beforeEach(() => {
      spies = [
        vi.spyOn(HTMLElement.prototype, "offsetTop", "get").mockReturnValue(0),
        vi
          .spyOn(HTMLElement.prototype, "offsetHeight", "get")
          .mockImplementation(function offsetHeight() {
            return this.getAttribute("role") === "tab" ? 40 : 0;
          }),
        vi
          .spyOn(Element.prototype, "clientHeight", "get")
          .mockImplementation(function clientHeight() {
            return this.classList.contains("MuiTabs-scroller") ? 80 : 0;
          }),
      ];
    });
    afterEach(() => {
      spies.forEach((spy) => spy.mockRestore());
    });

    it("draws the underline at the bottom of the open tab's own line", () => {
      mount("/dashboard/gateway/mcp?tab=servers");
      expect(
        screen.getByRole("tab", { name: "Servers", selected: true }),
      ).toBeInTheDocument();
      expect(cascaded(underline(), "bottom")).toBe("40px");
    });
  });
});

describe("the tab row at 1024 px", () => {
  // setupTests stubs the observer as a no-op; report the last tab as not
  // fully in view, as at 1024 px, where twelve tabs overflow the column.
  let SavedObserver;
  beforeEach(() => {
    SavedObserver = globalThis.IntersectionObserver;
    globalThis.IntersectionObserver = class {
      constructor(callback) {
        this.callback = callback;
      }

      observe() {
        this.callback([{ isIntersecting: false }]);
      }

      unobserve() {}

      disconnect() {}
    };
  });
  afterEach(() => {
    globalThis.IntersectionObserver = SavedObserver;
  });

  it("keeps every tab in view: the row wraps instead of scrolling", () => {
    mount("/dashboard/gateway/mcp?tab=connect");
    const row = screen.getByRole("tablist");
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(
      TAB_LABELS,
    );
    expect(cascaded(row, "flex-wrap")).toBe("wrap");
    const root = row.closest(".MuiTabs-root");
    expect(root.querySelector(".MuiTabs-scrollButtons")).toBeNull();
    expect(root.querySelector(".MuiTabs-scrollableX")).toBeNull();
    expect(
      screen.getByRole("tab", { name: "Connect", selected: true }),
    ).toBeInTheDocument();
  });
});
