import React from "react";
import {
  cleanup,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import CreateSandboxDrawer from "../create/CreateSandboxDrawer";
import {
  buildCreatePayload,
  chooseSize,
  chooseTemplate,
  initialForm,
  validateForm,
} from "../lib/createForm";
import {
  buildSettingsPayload,
  reconcileDefaults,
  settingsFormFrom,
  validateSettings,
} from "../lib/settingsForm";
import { CAPS_V2, fakeApi, renderSandboxes, settingsView } from "./helpers";
import { session, workspace } from "./session-mock";

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => workspace,
}));
vi.mock("src/sections/omega/shared", async (importOriginal) => ({
  ...(await importOriginal()),
  useOmegaSession: () => session,
}));

const { default: SandboxSettingsPage } = await import(
  "../settings/SandboxSettingsPage"
);
const { default: SandboxesListPage } = await import("../SandboxesListPage");

function open(caps = CAPS_V2, api = fakeApi()) {
  const onCreated = vi.fn();
  renderSandboxes(
    <CreateSandboxDrawer
      open
      caps={caps}
      onClose={vi.fn()}
      onCreated={onCreated}
    />,
    { api },
  );
  return { api, onCreated };
}

describe("create form with sizes and templates (unit)", () => {
  it("starts on the Small size and the default template, never on the largest size", () => {
    const form = initialForm(CAPS_V2);
    expect(form).toMatchObject({
      source: "catalog",
      template: "base",
      size: "small",
    });
    expect(buildCreatePayload(form)).toEqual({
      template: "base",
      size: "small",
      network: "none",
      idleTtlSeconds: 1800,
    });
  });

  it("applies a template's suggested size, moves a size the user picked up to the template's minimum, and refuses a size that is not offered", () => {
    const form = initialForm(CAPS_V2);
    // Browser suggests Large (offered here): the size follows the suggestion.
    expect(chooseTemplate(form, CAPS_V2, "browser")).toEqual({
      template: "browser",
      size: "large",
      cpus: "2",
      memoryMb: "4096",
      pidsLimit: "1024",
      sizeSuggested: "Large",
      sizeBumped: null,
    });
    // A size the user picked is kept unless it is below the minimum (Medium).
    const picked = { ...form, ...chooseSize(CAPS_V2, "small") };
    expect(chooseTemplate(picked, CAPS_V2, "browser")).toMatchObject({
      size: "medium",
      sizeBumped: "Medium",
      sizeSuggested: null,
    });
    expect(
      chooseTemplate(
        { ...form, ...chooseSize(CAPS_V2, "large") },
        CAPS_V2,
        "base",
      ),
    ).toEqual({ template: "base", sizeSuggested: null, sizeBumped: null });
    expect(validateForm({ ...form, size: "xl" }, CAPS_V2)).toEqual({
      size: "Choose a size offered here",
    });
    expect(
      validateForm({ ...form, template: "browser", size: "small" }, CAPS_V2),
    ).toEqual({ size: "Browser needs at least Medium" });
    // EE's host reason names a build script and says "yet": Future AGI's words instead.
    expect(
      validateForm({ ...form, template: "omega-agent" }, CAPS_V2).template,
    ).toBe("Not available on this deployment right now.");
  });

  it("goes back to the size the user picked once a template no longer needs more", () => {
    const picked = { ...initialForm(CAPS_V2), ...chooseSize(CAPS_V2, "small") };
    const browser = {
      ...picked,
      ...chooseTemplate(picked, CAPS_V2, "browser"),
    };
    expect(browser).toMatchObject({ size: "medium", sizeBumped: "Medium" });
    expect(chooseTemplate(browser, CAPS_V2, "base")).toEqual({
      template: "base",
      size: "small",
      cpus: "0.5",
      memoryMb: "1024",
      pidsLimit: "256",
      sizeSuggested: null,
      sizeBumped: null,
    });
  });

  it("sends custom limits only in custom mode, and an image from the allowed list", () => {
    const form = {
      ...initialForm(CAPS_V2),
      size: "custom",
      cpus: "1.5",
      memoryMb: "1536",
      pidsLimit: "300",
    };
    expect(buildCreatePayload(form)).toMatchObject({
      template: "base",
      limits: { cpus: 1.5, memoryMb: 1536, pidsLimit: 300 },
    });
    expect(
      buildCreatePayload({
        ...form,
        source: "image",
        image: "alpine:3.20",
        size: "medium",
      }),
    ).toEqual({
      image: "alpine:3.20",
      size: "medium",
      network: "none",
      idleTtlSeconds: 1800,
    });
  });
});

describe("Create sandbox drawer with sizes and templates", () => {
  it("shows every size with what it gives, disables the one the host cannot run, and says why", () => {
    open();
    expect(screen.getByTestId("create-size-small")).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByTestId("create-size-small").textContent).toContain(
      "0.5 vCPU · 1 GiB",
    );
    const xl = screen.getByTestId("create-size-xl");
    expect(xl).toBeDisabled();
    // EE's host reason names the "container host" (COPY1): Future AGI's words.
    expect(xl.textContent).toContain(
      "Too big for the machines this deployment runs sandboxes on.",
    );
    expect(xl.textContent).not.toMatch(/container host/);
    expect(screen.getByTestId("create-summary").textContent).toBe(
      "Base · Small, 0.5 vCPU · 1 GiB · network off",
    );
  });

  it("lists what a template installs and its scan, and creates the chosen template at the chosen size", async () => {
    const { api, onCreated } = open();
    expect(
      screen.getByTestId("create-template-contents").textContent,
    ).toContain("Node.js 22 with npm");
    // Every count the scan reports, not only the fixable ones (2 critical and
    // 70 high, none of them fixable yet, must not read as "no issues").
    expect(
      screen.getByTestId("create-template-contents").textContent,
    ).toContain(
      "scan: 2 critical and 70 high findings, none with a fix available",
    );
    expect(screen.getByTestId("create-template-omega-agent")).toBeDisabled();
    expect(
      screen.getByTestId("create-template-omega-agent").textContent,
    ).not.toMatch(/\byet\b|operator|build\.mjs/);
    fireEvent.click(screen.getByTestId("create-template-browser"));
    // The suggestion is applied, not only printed (console-reality audit).
    expect(screen.getByTestId("create-size-large")).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(
      within(screen.getByTestId("create-size-large")).getByTestId(
        "create-size-suggested",
      ),
    ).toHaveTextContent("Suggested");
    expect(screen.getByTestId("create-size-note").textContent).toBe(
      "Set to Large, the size suggested for this template. You can pick another.",
    );
    // The user's own pick wins and clears the note.
    fireEvent.click(screen.getByTestId("create-size-medium"));
    expect(screen.queryByTestId("create-size-note")).toBeNull();
    fireEvent.click(screen.getByTestId("create-submit"));
    await waitFor(() => expect(api.create).toHaveBeenCalledTimes(1));
    expect(api.create.mock.calls[0][0]).toEqual({
      template: "browser",
      size: "medium",
      network: "none",
      idleTtlSeconds: 1800,
    });
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
  });

  it("does not suggest a size this workspace cannot pick", () => {
    // Found in the live walk on Colima default: Browser said "Suggested size:
    // Large" while the Large card below was disabled for the host.
    open();
    fireEvent.click(screen.getByTestId("create-template-browser"));
    expect(screen.getByTestId("create-template-size-hint").textContent).toMatch(
      /^Suggested size: Large \(at least Medium\) · omega-sandbox\/browser/,
    );
    cleanup();
    open({
      ...CAPS_V2,
      sizes: CAPS_V2.sizes.map((size) =>
        size.id === "large"
          ? {
              ...size,
              available: false,
              refusedBy: "host",
              reason:
                "The container host has 3.8 GB of memory; this size needs 4 GB.",
            }
          : size,
      ),
    });
    fireEvent.click(screen.getByTestId("create-template-browser"));
    expect(screen.getByTestId("create-template-size-hint").textContent).toMatch(
      /^Suggested size: Large, not offered here \(runs on Medium or larger\) · omega-sandbox\/browser/,
    );
  });

  it("says what to fix instead of summarising limits the form refuses", async () => {
    // Found in the live walk: the footer read "3 vCPU · 6000 MB" next to the
    // fields refusing exactly those values.
    const { api } = open();
    fireEvent.click(screen.getByTestId("create-size-custom"));
    fireEvent.change(screen.getByTestId("create-cpus"), {
      target: { value: "3" },
    });
    fireEvent.change(screen.getByTestId("create-memoryMb"), {
      target: { value: "6000" },
    });
    fireEvent.click(screen.getByTestId("create-submit"));
    expect(screen.getByTestId("create-summary").textContent).toBe(
      "Fix the highlighted fields to create this sandbox.",
    );
    expect(api.create).not.toHaveBeenCalled();
    // Fixing one field still leaves the other refused.
    fireEvent.change(screen.getByTestId("create-cpus"), {
      target: { value: "1" },
    });
    expect(screen.getByTestId("create-summary").textContent).toBe(
      "Fix the highlighted fields to create this sandbox.",
    );
    // Back to a size: nothing refused any more, the summary returns.
    fireEvent.click(screen.getByTestId("create-size-custom"));
    expect(screen.getByTestId("create-summary").textContent).toBe(
      "Base · Small, 0.5 vCPU · 1 GiB · network off",
    );
  });

  it("explains network off and on in plain words", () => {
    open();
    expect(screen.getByTestId("create-network-explain").textContent).toMatch(
      /no network interface besides loopback/,
    );
    fireEvent.click(screen.getByTestId("create-network-toggle"));
    expect(screen.getByTestId("create-network-explain").textContent).toBe(
      "With networking selected, outbound access depends on this deployment's network controls. Where per-sandbox network policy is enabled, sandboxes created here start with outbound IP traffic blocked until you set an allowed policy through the Sandboxes API.",
    );
    expect(
      screen.getByTestId("create-network-explain").textContent,
    ).not.toMatch(/container host/i);
    expect(screen.getByTestId("create-summary").textContent).toContain(
      "network policy-controlled",
    );
  });
});

describe("Size and template choices by keyboard", () => {
  const press = (key) => fireEvent.keyDown(document.activeElement, { key });

  it("moves the size choice and focus with every arrow press, past the size that is not offered", () => {
    open();
    const card = (id) => screen.getByTestId(`create-size-${id}`);
    expect(card("small")).toHaveAttribute("tabindex", "0");
    expect(card("medium")).toHaveAttribute("tabindex", "-1");
    card("small").focus();
    press("ArrowRight");
    expect(card("medium")).toHaveAttribute("aria-checked", "true");
    expect(card("medium")).toHaveFocus();
    expect(card("medium")).toHaveAttribute("tabindex", "0");
    press("ArrowDown");
    expect(card("large")).toHaveAttribute("aria-checked", "true");
    expect(card("large")).toHaveFocus();
    // XL is not offered here, so the next press wraps to Small.
    press("ArrowRight");
    expect(card("small")).toHaveAttribute("aria-checked", "true");
    expect(card("small")).toHaveFocus();
    press("ArrowLeft");
    expect(card("large")).toHaveAttribute("aria-checked", "true");
    expect(card("large")).toHaveFocus();
  });

  it("gives the templates one tab stop and moves the choice and focus with the arrow keys", () => {
    open();
    const card = (id) => screen.getByTestId(`create-template-${id}`);
    expect(card("base")).toHaveAttribute("tabindex", "0");
    expect(card("python-data")).toHaveAttribute("tabindex", "-1");
    expect(card("browser")).toHaveAttribute("tabindex", "-1");
    card("base").focus();
    press("ArrowDown");
    expect(card("python-data")).toHaveAttribute("aria-checked", "true");
    expect(card("python-data")).toHaveFocus();
    press("ArrowRight");
    expect(card("browser")).toHaveAttribute("aria-checked", "true");
    expect(card("browser")).toHaveFocus();
    // Omega agent is not offered here, so the next press wraps to Base.
    press("ArrowRight");
    expect(card("base")).toHaveAttribute("aria-checked", "true");
    expect(card("base")).toHaveFocus();
    press("ArrowUp");
    expect(card("browser")).toHaveAttribute("aria-checked", "true");
    expect(card("browser")).toHaveFocus();
  });

  it("keeps the size the user picked after arrowing past a template that needs more", () => {
    open();
    const size = (id) => screen.getByTestId(`create-size-${id}`);
    const template = (id) => screen.getByTestId(`create-template-${id}`);
    fireEvent.click(size("small"));
    template("base").focus();
    // Base wraps back to Browser, which needs at least Medium.
    press("ArrowLeft");
    expect(template("browser")).toHaveAttribute("aria-checked", "true");
    expect(size("medium")).toHaveAttribute("aria-checked", "true");
    expect(screen.getByTestId("create-size-note").textContent).toBe(
      "Moved to Medium: this template needs at least that much.",
    );
    press("ArrowRight");
    expect(template("base")).toHaveAttribute("aria-checked", "true");
    expect(size("small")).toHaveAttribute("aria-checked", "true");
    expect(screen.queryByTestId("create-size-note")).toBeNull();
  });
});

describe("Settings → Sandboxes", () => {
  it("sends only what narrows the operator's settings", () => {
    const view = settingsView();
    const form = settingsFormFrom(view);
    expect(buildSettingsPayload(form, view)).toEqual({});
    const narrowed = {
      ...form,
      sizes: ["small", "medium"],
      defaultSize: "medium",
      allowNetwork: false,
      maxActive: "3",
    };
    expect(validateSettings(narrowed, view)).toEqual({});
    expect(buildSettingsPayload(narrowed, view)).toEqual({
      sizes: ["small", "medium"],
      defaultSize: "medium",
      allowNetwork: false,
      maxActive: 3,
    });
    expect(
      buildSettingsPayload({ ...form, defaultTemplate: "python-data" }, view),
    ).toEqual({ defaultTemplate: "python-data" });
    expect(validateSettings({ ...form, sizes: [] }, view)).toMatchObject({
      sizes: "Offer at least one size this deployment can run",
    });
    expect(validateSettings({ ...form, maxActive: "11" }, view)).toMatchObject({
      maxActive: "A whole number from 1 to 10",
    });
  });

  it("lets an owner narrow the sizes and saves them", async () => {
    const api = fakeApi();
    renderSandboxes(<SandboxSettingsPage />, { api });
    await screen.findByTestId("sandbox-settings");
    expect(screen.getByTestId("settings-size-xl")).toBeDisabled();
    // Found in the live walk: a size the host refuses looked exactly like a
    // live, checked choice. It is dimmed like its card in the create drawer.
    expect(screen.getByTestId("settings-size-xl").closest("label")).toHaveStyle(
      "opacity: 0.55",
    );
    expect(
      screen.getByTestId("settings-size-small").closest("label"),
    ).toHaveStyle("opacity: 1");
    // ...and it is not checked: it is not on offer, so it is never sent.
    expect(screen.getByTestId("settings-size-xl")).not.toBeChecked();
    fireEvent.click(screen.getByTestId("settings-size-large"));
    fireEvent.click(screen.getByTestId("settings-allow-network"));
    fireEvent.click(screen.getByTestId("sandbox-settings-save"));
    await waitFor(() => expect(api.saveSettings).toHaveBeenCalledTimes(1));
    expect(api.saveSettings.mock.calls[0][0]).toEqual({
      sizes: ["small", "medium"],
      allowNetwork: false,
    });
  });

  it("names this deployment's limits, never an operator (COPY1)", async () => {
    for (const allowNetwork of [true, false]) {
      const base = settingsView();
      const api = fakeApi({
        settings: vi.fn(async () =>
          settingsView({ operator: { ...base.operator, allowNetwork } }),
        ),
      });
      const { unmount } = renderSandboxes(<SandboxSettingsPage />, { api });
      await screen.findByTestId("sandbox-settings");
      expect(document.body).toHaveTextContent(
        "What people in this workspace can create, within this deployment's limits. You can narrow them here.",
      );
      expect(screen.getByTestId("settings-images")).toHaveTextContent(
        "Images this deployment allows outside the template catalog.",
      );
      if (!allowNetwork)
        expect(screen.getByTestId("settings-network")).toHaveTextContent(
          "This deployment offers sandboxes without a network only.",
        );
      expect(document.body.textContent).not.toMatch(/\boperators?\b/i);
      unmount();
    }
  });

  it("is read-only for a member", async () => {
    const api = fakeApi({
      settings: vi.fn(async () => settingsView({ canEdit: false })),
    });
    renderSandboxes(<SandboxSettingsPage />, { api });
    await screen.findByTestId("sandbox-settings-readonly");
    expect(screen.getByTestId("settings-size-small")).toBeDisabled();
    expect(screen.queryByTestId("sandbox-settings-save")).toBeNull();
  });
});

/**
 * The live repro on Colima default (2 CPU / 3.8 GB, ceiling 2 CPU / 4 GB):
 * Large is refused by the host and XL by the ceiling. Settings showed both
 * checked and locked, so unticking Small and Medium offered Large and XL as
 * the default and saved {"sizes":["large","xl"]}, and EE widened every new
 * sandbox to the ceiling.
 */
const COLIMA_SIZES = CAPS_V2.sizes.map((size) =>
  size.id === "large"
    ? {
        ...size,
        available: false,
        refusedBy: "host",
        reason:
          "The container host has 3.8 GB of memory; this size needs 4 GB.",
      }
    : size.id === "xl"
      ? {
          ...size,
          refusedBy: "ceiling",
          reason: "Above this deployment's sandbox limit (2 CPUs, 4 GB).",
        }
      : size,
);
const colimaView = (overrides = {}) =>
  settingsView({ caps: { ...CAPS_V2, sizes: COLIMA_SIZES }, ...overrides });
const REFUSAL = {
  field: "size",
  reason:
    "This workspace offers only Large and XL, and none of them can run here. A workspace owner or admin can offer Small and Medium in the workspace's sandbox settings.",
};

describe("Settings never offer what the deployment cannot run", () => {
  it("shows sizes the host or the ceiling refuses unchecked, never as the default, and never sends them", async () => {
    const api = fakeApi({ settings: vi.fn(async () => colimaView()) });
    renderSandboxes(<SandboxSettingsPage />, { api });
    await screen.findByTestId("sandbox-settings");
    for (const id of ["large", "xl"]) {
      expect(screen.getByTestId(`settings-size-${id}`)).toBeDisabled();
      expect(screen.getByTestId(`settings-size-${id}`)).not.toBeChecked();
    }
    expect(screen.getByTestId("settings-size-large-detail").textContent).toBe(
      "Too big for the machines this deployment runs sandboxes on.",
    );
    // EE's ceiling reason is already in plain words and stays.
    expect(screen.getByTestId("settings-size-xl-detail").textContent).toBe(
      "Above this deployment's sandbox limit (2 CPUs, 4 GB).",
    );
    fireEvent.click(screen.getByTestId("settings-size-small"));
    fireEvent.click(screen.getByTestId("settings-size-medium"));
    expect(
      screen.getByText("Offer at least one size this deployment can run"),
    ).toBeInTheDocument();
    // One thing to fix: the templates do not each repeat that no size is offered.
    expect(
      screen.getByTestId("settings-template-base-detail").textContent,
    ).toBe("Python and Node.");
    expect(screen.queryByTestId("settings-templates-error")).toBeNull();
    expect(screen.getByTestId("sandbox-settings-save")).toBeDisabled();
    // The default size offers nothing the workspace could not run.
    fireEvent.mouseDown(
      within(screen.getByTestId("settings-sizes")).getByRole("combobox"),
    );
    expect(screen.queryByRole("option", { name: "Large" })).toBeNull();
    expect(screen.queryByRole("option", { name: "XL" })).toBeNull();
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Escape" });
    fireEvent.click(screen.getByTestId("settings-size-medium"));
    fireEvent.click(screen.getByTestId("sandbox-settings-save"));
    await waitFor(() => expect(api.saveSettings).toHaveBeenCalledTimes(1));
    expect(api.saveSettings.mock.calls[0][0]).toEqual({
      sizes: ["medium"],
      defaultSize: "medium",
    });
  });

  it("reads a stored set nobody can run as offering nothing, and builds no body that names those sizes", () => {
    const view = colimaView({
      settings: { sizes: ["large", "xl"], defaultSize: "large" },
    });
    const form = settingsFormFrom(view);
    expect(form.sizes).toEqual([]);
    expect(form.defaultSize).toBe("");
    expect(validateSettings(form, view).sizes).toBe(
      "Offer at least one size this deployment can run",
    );
    // Offering Small again also brings both defaults back among what is offered.
    const fixed = reconcileDefaults({ ...form, sizes: ["small"] }, view);
    expect(fixed).toMatchObject({
      defaultSize: "small",
      defaultTemplate: "base",
    });
    expect(validateSettings(fixed, view)).toEqual({});
    expect(buildSettingsPayload(fixed, view)).toEqual({ sizes: ["small"] });
  });

  it("marks a template that needs a bigger size, keeps it out of the default list, and refuses a set where none fits", async () => {
    const api = fakeApi({
      settings: vi.fn(async () =>
        colimaView({ operator: { ...settingsView().operator, images: [] } }),
      ),
    });
    renderSandboxes(<SandboxSettingsPage />, { api });
    await screen.findByTestId("sandbox-settings");
    // The image the host does not have is unchecked and says why.
    expect(screen.getByTestId("settings-template-omega-agent")).toBeDisabled();
    expect(
      screen.getByTestId("settings-template-omega-agent"),
    ).not.toBeChecked();
    fireEvent.click(screen.getByTestId("settings-size-medium"));
    expect(
      screen.getByTestId("settings-template-browser-detail").textContent,
    ).toBe("Needs at least Medium, which this workspace does not offer");
    fireEvent.mouseDown(
      within(screen.getByTestId("settings-templates")).getByRole("combobox"),
    );
    expect(screen.queryByRole("option", { name: "Browser" })).toBeNull();
    expect(screen.getByRole("option", { name: "Base" })).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Escape" });
    fireEvent.click(screen.getByTestId("settings-template-base"));
    fireEvent.click(screen.getByTestId("settings-template-python-data"));
    expect(screen.getByTestId("settings-templates-error").textContent).toBe(
      "None of these templates fits the sizes offered; offer a bigger size or another template",
    );
    expect(screen.getByTestId("sandbox-settings-save")).toBeDisabled();
    expect(api.saveSettings).not.toHaveBeenCalled();
  });

  it("says plainly when nothing can be created, on the settings page, the list page and in the drawer", async () => {
    const caps = { ...CAPS_V2, sizes: COLIMA_SIZES, createRefusal: REFUSAL };
    const api = fakeApi({
      settings: vi.fn(async () => settingsView({ caps })),
      list: vi.fn(async () => ({ sandboxes: [], caps, provider: {} })),
    });
    renderSandboxes(<SandboxSettingsPage />, { api });
    expect(
      (await screen.findByTestId("sandbox-settings-refusal")).textContent,
    ).toContain(REFUSAL.reason);
    cleanup();
    renderSandboxes(<SandboxesListPage />, { api });
    expect(
      (await screen.findByTestId("sandboxes-create-refusal")).textContent,
    ).toContain(REFUSAL.reason);
    cleanup();
    const { api: drawerApi } = open(caps);
    expect(screen.getByTestId("create-refusal").textContent).toContain(
      REFUSAL.reason,
    );
    expect(screen.queryByTestId("create-size")).toBeNull();
    expect(screen.queryByTestId("create-custom-limits")).toBeNull();
    expect(screen.getByTestId("create-submit")).toBeDisabled();
    fireEvent.click(screen.getByTestId("create-refusal-settings"));
    expect(screen.getByTestId("location").textContent).toBe(
      "/dashboard/settings/sandboxes",
    );
    expect(drawerApi.create).not.toHaveBeenCalled();
  });
});

describe("Sandboxes page link to settings", () => {
  it("shows Settings to someone who can change them, and not to anyone else", async () => {
    const api = fakeApi({
      list: vi.fn(async () => ({ sandboxes: [], caps: CAPS_V2, provider: {} })),
    });
    renderSandboxes(<SandboxesListPage />, { api });
    expect(
      await screen.findByTestId("sandbox-settings-link"),
    ).toBeInTheDocument();
  });

  it("hides Settings from a member", async () => {
    const api = fakeApi({
      list: vi.fn(async () => ({
        sandboxes: [],
        caps: { ...CAPS_V2, settings: { canEdit: false } },
        provider: {},
      })),
    });
    renderSandboxes(<SandboxesListPage />, { api });
    await screen.findByTestId("sandboxes-empty");
    expect(screen.queryByTestId("sandbox-settings-link")).toBeNull();
  });
});
