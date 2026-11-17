import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useParams,
} from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { ThemeProvider } from "@mui/material/styles";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import AlertsSection from "./AlertsSection";
import { faTestTheme } from "../__tests__/faTheme";
import { NOT_ADMIN_LEVELS, expectAdminOnly } from "../testing/adminOnly";

vi.mock("ag-grid-react", () => import("../logs/__tests__/fakeAgGrid"));

const state = vi.hoisted(() => ({
  orgLevel: 8,
  config: null,
  loading: false,
  error: null,
  mutate: vi.fn(),
}));

vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: state.orgLevel }),
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "default", isLoading: false }),
}));
vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({
    data: state.config,
    isLoading: state.loading,
    error: state.error,
  }),
  useUpdateConfig: () => ({ mutate: state.mutate, isPending: false }),
}));

function RoutedAlerts() {
  const { tab } = useParams();
  const { pathname } = useLocation();
  return (
    <>
      <output aria-label="Current page">{pathname}</output>
      <AlertsSection tab={tab} />
    </>
  );
}

function renderAt(tab = "rules") {
  return render(
    <HelmetProvider>
      <ThemeProvider theme={faTestTheme("light")}>
        <MemoryRouter initialEntries={[`/dashboard/gateway/monitoring/${tab}`]}>
          <Routes>
            <Route
              path="/dashboard/gateway/monitoring/:tab"
              element={<RoutedAlerts />}
            />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </HelmetProvider>,
  );
}

beforeEach(() => {
  state.orgLevel = 8;
  state.config = { alerting: { enabled: true, rules: [], channels: [] } };
  state.loading = false;
  state.error = null;
  state.mutate.mockReset();
});

describe("Alert setup navigation", () => {
  it.each([
    ["page", 0],
    ["empty-state", 1],
  ])(
    "opens Channels and its create dialog from the %s Add channel action",
    async (_source, index) => {
      const user = userEvent.setup();
      renderAt();
      expect(screen.getByText("No alert rules yet")).toBeInTheDocument();
      expect(
        screen.getByText(
          "Add a channel first, then create a rule that notifies it.",
        ),
      ).toBeInTheDocument();
      await user.click(
        screen.getAllByRole("button", { name: "Add channel" })[index],
      );
      expect(screen.getByLabelText("Current page")).toHaveTextContent(
        "/dashboard/gateway/monitoring/channels",
      );
      const dialog = screen.getByRole("dialog");
      expect(dialog).toHaveTextContent("Add notification channel");
      expect(within(dialog).getByLabelText(/Channel name/)).toBeEnabled();
      expect(
        within(dialog).queryByLabelText(/Rule name/),
      ).not.toBeInTheDocument();
      expect(state.mutate).not.toHaveBeenCalled();
      await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
      );
      expect(screen.getByLabelText("Current page")).toHaveTextContent(
        "/monitoring/channels",
      );
      expect(screen.getByRole("tab", { name: "Channels 0" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
      await user.click(screen.getByRole("tab", { name: "Rules 0" }));
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(state.mutate).not.toHaveBeenCalled();
    },
  );

  it("keeps Create rule on Rules when a notification channel already exists", async () => {
    state.config.alerting.channels = [{ name: "ops", type: "log" }];
    const user = userEvent.setup();
    renderAt();
    await user.click(screen.getAllByRole("button", { name: "Create rule" })[1]);
    expect(screen.getByLabelText("Current page")).toHaveTextContent(
      "/monitoring/rules",
    );
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByLabelText(/Rule name/)).toBeEnabled();
    expect(
      within(dialog).getByRole("combobox", { name: "Notify channels" }),
    ).not.toHaveAttribute("aria-disabled", "true");
    expect(
      within(dialog).queryByLabelText(/Channel name/),
    ).not.toBeInTheDocument();
    expect(state.mutate).not.toHaveBeenCalled();
  });

  it("keeps the direct Channels Add channel flow", async () => {
    const user = userEvent.setup();
    renderAt("channels");
    await user.click(screen.getAllByRole("button", { name: "Add channel" })[1]);
    expect(screen.getByRole("dialog")).toHaveTextContent(
      "Add notification channel",
    );
    expect(screen.getByLabelText("Current page")).toHaveTextContent(
      "/monitoring/channels",
    );
  });

  it.each(NOT_ADMIN_LEVELS)(
    "keeps %s read-only when no channels exist",
    (_role, level) => {
      state.orgLevel = level;
      renderAt();
      expect(screen.getByText("No alert rules yet")).toBeInTheDocument();
      const action = screen.getByRole("button", { name: "Add channel" });
      expectAdminOnly(action);
      fireEvent.click(action);
      expect(screen.getByLabelText("Current page")).toHaveTextContent(
        "/monitoring/rules",
      );
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(state.mutate).not.toHaveBeenCalled();
    },
  );

  it.each(["loading", "error"])(
    "does not start channel setup while the config is %s",
    (status) => {
      state.config = undefined;
      state.loading = status === "loading";
      state.error = status === "error" ? new Error("Config read failed") : null;
      renderAt();
      const action = screen.getByRole("button", { name: "Create rule" });
      expect(action).toBeDisabled();
      fireEvent.click(action);
      expect(screen.getByLabelText("Current page")).toHaveTextContent(
        "/monitoring/rules",
      );
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(state.mutate).not.toHaveBeenCalled();
    },
  );
});
