import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { ThemeProvider } from "@mui/material/styles";

import AlertsSection from "./AlertsSection";
import { faTestTheme } from "../__tests__/faTheme";
import { grid } from "../logs/__tests__/fakeAgGrid";
import { cutHeaders, gridBudget1440, minimumGridWidth } from "../logs/__tests__/gridFit";

vi.mock("ag-grid-react", () => import("../logs/__tests__/fakeAgGrid"));
const state = vi.hoisted(() => ({ config: null, latest: null, level: 8, refetch: vi.fn(), mutate: vi.fn(), mutateAsync: vi.fn(), toast: vi.fn() }));
vi.mock("notistack", () => ({ enqueueSnackbar: state.toast }));
vi.mock("src/contexts/OrganizationContext", () => ({ useOrganization: () => ({ orgLevel: state.level }) }));
vi.mock("../context/useGatewayContext", () => ({ useGatewayContext: () => ({ gatewayId: "default", isLoading: false }) }));
vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({ data: state.config, isLoading: false, refetch: state.refetch }),
  useUpdateConfig: () => ({ mutate: state.mutate, mutateAsync: state.mutateAsync, isPending: false }),
}));

const target = { name: "errors", metric: "error_count", threshold: 5, enabled: true, channels: ["ops"] };
const other = { name: "cost", threshold: 30, custom: { keep: [null, 1] } };
const makeConfig = (rules = [target, other]) => ({
  org_sections_enforced: ["alerting"],
  alerting: { enabled: true, rules, channels: [{ name: "ops", type: "log" }] },
});

const view = () => (
  <HelmetProvider><ThemeProvider theme={faTestTheme("light")}>
    <MemoryRouter><AlertsSection /></MemoryRouter>
  </ThemeProvider></HelmetProvider>
);

beforeEach(() => {
  state.config = makeConfig();
  state.latest = makeConfig([{ ...target, unknown: { added: true } }, { ...other, threshold: 99 }, { name: "new-rule" }]);
  state.level = 8;
  state.refetch.mockReset().mockImplementation(async () => ({ data: state.latest }));
  state.mutate.mockReset();
  state.toast.mockReset();
  state.mutateAsync.mockReset().mockResolvedValue({ gateway_synced: true });
});

describe("alert rule edit and pause actions", () => {
  it("keeps the new actions within the existing desktop grid budget", () => {
    render(view());
    expect(cutHeaders(grid.props)).toEqual([]);
    expect(minimumGridWidth(grid.props)).toBeLessThanOrEqual(gridBudget1440());
  });

  it("reads the latest server copy before editing and retains unrelated changes", async () => {
    const user = userEvent.setup();
    render(view());
    await user.click(screen.getByRole("button", { name: "Edit rule errors" }));
    const dialog = within(screen.getByRole("dialog"));
    await user.clear(dialog.getByLabelText(/Threshold/));
    await user.type(dialog.getByLabelText(/Threshold/), "7");
    await user.click(dialog.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(state.mutateAsync).toHaveBeenCalledOnce());
    expect(state.refetch).toHaveBeenCalledWith({ throwOnError: true });
    expect(state.mutateAsync).toHaveBeenCalledWith({
      gatewayId: "default",
      config: { alerting: { rules: [{ ...state.latest.alerting.rules[0], threshold: 7 }, ...state.latest.alerting.rules.slice(1)] } },
    });
    expect(state.refetch.mock.invocationCallOrder[0]).toBeLessThan(state.mutateAsync.mock.invocationCallOrder[0]);
    expect(state.mutate).not.toHaveBeenCalled();
  });

  it("persists pause, reopens as disabled, then resumes without enabling org alerting", async () => {
    const user = userEvent.setup();
    state.latest.alerting.enabled = false;
    const rendered = render(view());
    await user.click(screen.getByRole("button", { name: "Pause rule errors" }));
    await waitFor(() => expect(state.mutateAsync).toHaveBeenCalledOnce());
    const paused = state.mutateAsync.mock.calls[0][0].config.alerting;
    expect(paused.rules).toEqual([{ ...state.latest.alerting.rules[0], enabled: false }, ...state.latest.alerting.rules.slice(1)]);
    expect(paused).not.toHaveProperty("enabled");
    state.config = { ...state.latest, alerting: { ...state.latest.alerting, ...paused } };
    state.latest = state.config;
    rendered.unmount();
    render(view());
    const row = screen.getByRole("button", { name: "Resume rule errors" }).closest("tr");
    expect(within(row).getByText("Disabled")).toBeInTheDocument();
    await user.click(within(row).getByRole("button", { name: "Resume rule errors" }));
    await waitFor(() => expect(state.mutateAsync).toHaveBeenCalledTimes(2));
    expect(state.mutateAsync.mock.calls[1][0].config.alerting.rules[0].enabled).toBe(true);
    expect(state.mutateAsync.mock.calls[1][0].config.alerting).not.toHaveProperty("enabled");
  });

  it.each(["rejected", "cached-error"])("does not write after a %s latest read", async (mode) => {
    if (mode === "rejected") state.refetch.mockRejectedValue(new Error("Latest config unavailable"));
    else state.refetch.mockResolvedValue({ data: state.latest, isError: true, error: new Error("Latest config unavailable") });
    const user = userEvent.setup();
    render(view());
    await user.click(screen.getByRole("button", { name: "Pause rule errors" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/latest config|could not read/i);
    expect(state.mutateAsync).not.toHaveBeenCalled();
  });

  it.each([{ statusCode: 409 }, { response: { status: 412 } }])("refuses a conflict (%j) and offers reload rather than retrying the write", async (error) => {
    state.mutateAsync.mockRejectedValue(error);
    const user = userEvent.setup();
    render(view());
    await user.click(screen.getByRole("button", { name: "Pause rule errors" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/changed.*reload/i);
    expect(state.mutateAsync).toHaveBeenCalledOnce();
    await user.click(screen.getByRole("button", { name: "Reload rules" }));
    expect(state.refetch).toHaveBeenCalledTimes(2);
    expect(state.mutateAsync).toHaveBeenCalledOnce();
  });

  it.each([false, undefined])("never reports pause applied with gateway_synced=%s", async (synced) => {
    if (synced === false) {
      state.mutateAsync.mockRejectedValue(Object.assign(new Error("Saved, but the gateway did not apply it."), { savedButNotApplied: true }));
    } else state.mutateAsync.mockResolvedValue({});
    const user = userEvent.setup();
    render(view());
    await user.click(screen.getByRole("button", { name: "Pause rule errors" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/saved.*gateway.*(apply|confirm)/i);
    expect(screen.getByText("Apply not confirmed")).toBeInTheDocument();
    expect(state.toast).not.toHaveBeenCalledWith(expect.anything(), { variant: "success" });
  });

  it("serializes actions while awaiting the latest config", async () => {
    let finish;
    state.refetch.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    const user = userEvent.setup();
    render(view());
    await user.click(screen.getByRole("button", { name: "Pause rule errors" }));
    expect(screen.getByRole("button", { name: "Edit rule cost" })).toBeDisabled();
    const pause = screen.getByRole("button", { name: "Pause rule errors" });
    expect(pause).toBeDisabled();
    fireEvent.click(pause);
    expect(state.refetch).toHaveBeenCalledOnce();
    expect(state.mutateAsync).not.toHaveBeenCalled();
    await act(async () => finish({ data: state.latest }));
    expect(state.mutateAsync).toHaveBeenCalledOnce();
  });

  it("keeps non-admin edit and pause read-only", () => {
    state.level = 5;
    render(view());
    for (const label of ["Edit rule errors", "Pause rule errors"]) {
      const button = screen.getByRole("button", { name: label });
      expect(button).toBeDisabled();
      fireEvent.click(button);
    }
    expect(state.refetch).not.toHaveBeenCalled();
    expect(state.mutateAsync).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("alert rule creation", () => {
  async function create(user, name) {
    await user.click(screen.getByRole("button", { name: "Create rule" }));
    const dialog = within(screen.getByRole("dialog"));
    await user.type(dialog.getByLabelText(/Rule name/), name);
    await user.type(dialog.getByLabelText(/Threshold/), "99");
    await user.click(dialog.getByRole("button", { name: "Create rule" }));
    return dialog;
  }

  it("refuses a name present only in the latest server config", async () => {
    const user = userEvent.setup();
    state.latest = makeConfig([...state.config.alerting.rules, { name: "new-rule", threshold: 5, enabled: false, channels: ["ops"] }]);
    render(view());
    const dialog = await create(user, "new-rule");
    expect(await dialog.findByRole("alert")).toHaveTextContent(/already exists.*Edit/i);
    expect(state.refetch).toHaveBeenCalledWith({ throwOnError: true });
    expect(state.mutate).not.toHaveBeenCalled();
    expect(state.mutateAsync).not.toHaveBeenCalled();
  });

  it("keeps all latest rules when creating a unique name", async () => {
    const user = userEvent.setup();
    render(view());
    await create(user, "unique-rule");
    await waitFor(() => expect(state.mutate).toHaveBeenCalledOnce());
    expect(state.refetch).toHaveBeenCalledWith({ throwOnError: true });
    expect(state.refetch.mock.invocationCallOrder[0]).toBeLessThan(state.mutate.mock.invocationCallOrder[0]);
    expect(state.mutate.mock.calls[0][0]).toEqual({
      gatewayId: "default",
      config: { alerting: { enabled: true, rules: [
        ...state.latest.alerting.rules,
        expect.objectContaining({ name: "unique-rule", threshold: 99, enabled: true, channels: [] }),
      ] } },
    });
  });

  it.each(["rejected", "cached-error", "missing-data"])("does not create after a %s latest read", async (mode) => {
    if (mode === "rejected") state.refetch.mockRejectedValue(new Error("Could not read the latest rules."));
    else if (mode === "cached-error") state.refetch.mockResolvedValue({ data: state.latest, isError: true, error: new Error("Read failed") });
    else state.refetch.mockResolvedValue({ data: undefined });
    const user = userEvent.setup();
    render(view());
    const dialog = await create(user, "unique-rule");
    expect(await dialog.findByRole("alert")).toHaveTextContent(/could not read the latest rules/i);
    expect(state.mutate).not.toHaveBeenCalled();
    expect(dialog.getByLabelText(/Rule name/)).toHaveValue("unique-rule");
  });
});
