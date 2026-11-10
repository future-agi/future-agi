import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createTheme, ThemeProvider } from "@mui/material/styles";
import { MemoryRouter } from "react-router-dom";

import HostUpdatesPage from "../HostUpdatesPage";
import {
  automaticNoticeForStatus,
  normalizeHostUpdateStatus,
} from "src/api/hostUpdates/host-update-status";
import HostUpdateNotice from "src/components/host-update-status/HostUpdateNotice";
import {
  fixtures,
  projectedStatusFixtures,
  responseFixture,
  restoreResponse,
  schema,
  statusFixture,
} from "src/api/hostUpdates/__tests__/fixtures";
import {
  ACTIVE_STATES,
  LABELS,
  OPERATOR_ACTIONS,
  REASON_TEXT,
  UNAVAILABLE_REASONS,
} from "../explainer-sections";

const mocks = vi.hoisted(() => ({ query: {} }));
vi.mock("src/api/hostUpdates/host-update-status", async (importOriginal) => ({
  ...(await importOriginal()),
  useHostUpdateStatus: () => mocks.query,
}));

beforeEach(() => {
  mocks.query = {
    allowed: true,
    pending: false,
    data: responseFixture().result,
    isLoading: false,
    isFetching: false,
    canRefresh: true,
    refresh: vi.fn(),
  };
});
afterEach(cleanup);

function renderPage(mode = "light") {
  return render(
    <ThemeProvider theme={createTheme({ palette: { mode } })}>
      <HostUpdatesPage />
    </ThemeProvider>,
  );
}
function notice() {
  return render(
    <MemoryRouter>
      <HostUpdateNotice />
    </MemoryRouter>,
  );
}
function field(label) {
  return screen.getAllByText(label, { selector: "dt" })[0].nextElementSibling;
}

describe("closed report states", () => {
  it.each(fixtures.valid_api)(
    "renders API fixture $name without assuming health",
    ({ response }) => {
      mocks.query.data = structuredClone(response.result);
      renderPage();
      expect(screen.getByTestId("host-updates-page")).toBeInTheDocument();
      expect(screen.getByTestId("host-updates-summary")).toHaveAttribute(
        "role",
        "status",
      );
      if (response.result.availability === "available") {
        expect(screen.getByTestId("host-updates-installed-version")).toBe(
          field("Running release (last observed)"),
        );
        expect(screen.getByTestId("host-updates-available-version")).toBe(
          field("Verified candidate (last check)"),
        );
        expect(
          screen.getByTestId("host-updates-installed-version").textContent,
        ).toBe(response.result.snapshot.versions.running_release || "Unknown");
        expect(
          screen.getByTestId("host-updates-available-version").textContent,
        ).toBe(
          response.result.snapshot.discovery.candidate_version ||
            "No verified candidate reported",
        );
        expect(screen.getByTestId("host-updates-version-freshness")).toBe(
          field("Version freshness"),
        );
        expect(screen.getByTestId("host-updates-check-freshness")).toBe(
          field("Check freshness"),
        );
        expect(
          screen.queryByTestId("host-updates-unavailable"),
        ).not.toBeInTheDocument();
        expect(
          screen.getByRole("heading", { name: "Selected and running" }),
        ).toBeInTheDocument();
        expect(field("Selected release")).toHaveTextContent(
          response.result.snapshot.versions.selected_release || "Unknown",
        );
        expect(field("Check freshness")).toHaveTextContent(
          LABELS.freshness[response.result.discovery_freshness],
        );
      } else {
        expect(screen.getByTestId("host-updates-unavailable").textContent).toBe(
          response.result.availability === "unsupported"
            ? "Reporting unsupported"
            : "Reporting unavailable",
        );
        expect(
          screen.queryByTestId("host-updates-installed-version"),
        ).not.toBeInTheDocument();
        expect(
          screen.queryByTestId("host-updates-available-version"),
        ).not.toBeInTheDocument();
        expect(
          screen.getByText(UNAVAILABLE_REASONS[response.result.reason_code]),
        ).toBeInTheDocument();
        expect(
          screen.queryByRole("heading", { name: "Selected and running" }),
        ).not.toBeInTheDocument();
        expect(
          screen.getByText(
            "Update availability, running versions and automation are unknown.",
          ),
        ).toBeInTheDocument();
      }
    },
  );
  it.each(fixtures.valid_status)("renders host snapshot $name", ({ name }) => {
    const data = statusFixture(name).result;
    mocks.query.data = data;
    renderPage();
    expect(field("Effective state (as reported)")).toHaveTextContent(
      LABELS.automation[data.snapshot.automation.state],
    );
    expect(field("Download")).toHaveTextContent(
      LABELS.download[data.snapshot.download.state],
    );
    expect(field("Runtime observations")).toHaveTextContent(
      LABELS.runtime[data.snapshot.versions.runtime_state],
    );
  });
  const stateGroups = [
    ["operation", "state", "operation", "Operation (last reported)"],
    ["operation", "stage", "stage", "Stage"],
    ["operation", "kind", "kind", "Operation kind"],
    ["download", "state", "download", "Download"],
    ["discovery", "state", "discovery", "Last check result"],
    ["discovery", "verification", "verification", "Verification"],
    ["automation", "mode", "mode", "Configured mode"],
    ["automation", "state", "automation", "Effective state (as reported)"],
    ["versions", "runtime_state", "runtime", "Runtime observations"],
    ["policy", "channel", "channel", "Channel and policy"],
    ["policy", "version_policy", "policy", "Channel and policy"],
    ["safety", "backup_state", "backup", "Backup result"],
    ["safety", "readiness", "readiness", "Readiness"],
    ["safety", "automatic_rollback", "rollback", "Automatic image rollback"],
  ];
  stateGroups.forEach(([section, key, labels, name]) => {
    const property = schema.properties[section].properties[key];
    const values = property.enum || property.anyOf[0].enum;
    it.each(values)(
      `renders ${section}.${key}=%s as explicit text`,
      (value) => {
        mocks.query.data.snapshot[section][key] = value;
        renderPage();
        expect(field(name)).toHaveTextContent(LABELS[labels][value]);
      },
    );
    it(`has a label for every ${section}.${key} in the contract`, () => {
      expect(Object.keys(LABELS[labels]).sort()).toEqual([...values].sort());
    });
  });
  it.each(schema.properties.safety.properties.operator_action.enum)(
    "explains host-only operator action %s",
    (action) => {
      mocks.query.data.snapshot.safety.operator_action = action;
      renderPage();
      expect(screen.getByText(OPERATOR_ACTIONS[action])).toBeInTheDocument();
    },
  );
  it.each(schema.properties.operation.properties.reason_codes.items.enum)(
    "renders safe reason %s",
    (code) => {
      mocks.query.data.snapshot.operation.reason_codes = [code];
      renderPage();
      expect(
        screen.getByText(REASON_TEXT[code], { selector: "li" }),
      ).toBeInTheDocument();
    },
  );
  it("has fixed copy for every reason in the contract", () => {
    expect(Object.keys(REASON_TEXT).sort()).toEqual(
      [
        ...schema.properties.operation.properties.reason_codes.items.enum,
      ].sort(),
    );
  });
  it.each(schema.properties.last_run.anyOf[0].properties.outcome.enum)(
    "preserves forward outcome %s separately from fallback",
    (outcome) => {
      mocks.query.data.snapshot.last_run = {
        kind: "apply",
        automatic: true,
        outcome,
        from_version: "v1.2.3",
        target_version: "v1.2.4",
        finished_at: "2026-10-24T12:00:00Z",
        fallback_outcome: "succeeded",
      };
      renderPage();
      expect(field("Forward outcome")).toHaveTextContent(
        LABELS.outcome[outcome],
      );
      expect(field("Fallback result")).toHaveTextContent(
        "Prior-image fallback succeeded",
      );
      expect(
        screen.getByText(
          /Image rollback does not restore databases or undo migrations/,
        ),
      ).toBeInTheDocument();
    },
  );
  it.each(schema.properties.last_run.anyOf[0].properties.fallback_outcome.enum)(
    "shows fallback outcome %s",
    (fallback) => {
      mocks.query.data.snapshot.last_run = {
        kind: "rollback",
        automatic: false,
        outcome: "failed_during_rollout",
        from_version: null,
        target_version: null,
        finished_at: null,
        fallback_outcome: fallback,
      };
      renderPage();
      expect(field("Fallback result")).toHaveTextContent(
        LABELS.fallback[fallback],
      );
    },
  );
  it("distinguishes selected target from running version during production staging", () => {
    mocks.query.data = statusFixture(
      "production_stage_selected_differs_from_running",
    ).result;
    renderPage();
    expect(field("Selected release")).toHaveTextContent("v1.2.4");
    expect(field("Running release (last observed)")).toHaveTextContent(
      "v1.2.3",
    );
    expect(screen.getByText(/Stage is not apply/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", {
        name: /^(apply|download|rollback|pause|enable)( update)?$/i,
      }),
    ).not.toBeInTheDocument();
  });
  it("does not renew old discovery or progress from a fresh heartbeat", () => {
    mocks.query.data = responseFixture(
      "heartbeat_does_not_refresh_observations",
    ).result;
    renderPage();
    expect(field("Last check result")).toHaveTextContent("Previously current");
    expect(field("Check freshness")).toHaveTextContent("Stale");
    expect(field("Version freshness")).toHaveTextContent("Stale");
  });
  it("shows stale progress without diagnosing a failed operation", () => {
    mocks.query.data = responseFixture(
      "active_stale_progress_independent_of_transport",
    ).result;
    renderPage();
    expect(field("Progress freshness")).toHaveTextContent("Stale");
    expect(
      screen.getByText(/Old progress does not prove failure/),
    ).toBeInTheDocument();
    expect(field("Operation (last reported)")).not.toHaveTextContent("failed");
  });
  it("shows timezone, report-date offset, overnight window and next permitted start", () => {
    mocks.query.data = statusFixture("cross_midnight_window").result;
    mocks.query.data.snapshot.operation.reason_codes = ["outside_window"];
    renderPage();
    const window = mocks.query.data.snapshot.automation.maintenance_window;
    expect(field("Maintenance window")).toHaveTextContent(window.timezone);
    expect(field("Maintenance window")).toHaveTextContent(
      `${window.start}–${window.end}`,
    );
    expect(field("Maintenance window")).toHaveTextContent("at report time");
    expect(
      screen.getByText(REASON_TEXT.outside_window, { selector: "li" }),
    ).toBeInTheDocument();
  });
  it("shows an always-open window without inventing a schedule", () => {
    mocks.query.data.snapshot.automation.maintenance_window = {
      kind: "always",
      timezone: "UTC",
    };
    renderPage();
    expect(field("Maintenance window")).toHaveTextContent("Any time — UTC");
    expect(field("Next action (as reported)")).toHaveTextContent(
      "Not reported",
    );
  });
});

describe("restore-backed details and notice destination", () => {
  beforeEach(() => {
    mocks.query.data = restoreResponse().result;
    mocks.query.automaticNotice = automaticNoticeForStatus(mocks.query.data);
  });
  it.each([
    ["restoring", "running", "restoring_volumes"],
    ["restored", "verified", "restored"],
    ["recovery_required", "recovery_required", "restore_quiescing"],
  ])(
    "shows %s with separate forward and restoration results",
    (state, restore, phase) => {
      const snapshot = mocks.query.data.snapshot;
      Object.assign(snapshot.operation, { state, phase });
      snapshot.safety.restore_state = restore;
      snapshot.last_run.restore_result = restore;
      snapshot.automation.enabled = false;
      snapshot.automation.state = "disabled";
      if (restore !== "verified") {
        Object.assign(snapshot.versions, {
          running_release: null,
          runtime_state: "unknown",
        });
        snapshot.last_run.effective_version = null;
      }
      mocks.query.automaticNotice = automaticNoticeForStatus(mocks.query.data);
      const banner = notice();
      expect(
        screen.getByRole("link", { name: "View update details" }),
      ).toHaveAttribute("href", "/dashboard/settings/updates");
      expect(screen.getByRole("status")).toHaveTextContent(
        mocks.query.automaticNotice.text,
      );
      banner.unmount();
      renderPage();
      expect(field("Operation (last reported)").textContent).toBe(
        LABELS.operation[state],
      );
      expect(field("Operation kind").textContent).toBe(
        "Cold-backup restoration",
      );
      expect(field("Last verified phase").textContent).toBe(
        LABELS.phase[phase],
      );
      expect(field("Backup result").textContent).toBe(
        "Cold backup sealed for this attempt",
      );
      expect(field("Forward result").textContent).toBe("Failed");
      expect(field("Forward outcome").textContent).toBe(
        "Failed during service replacement",
      );
      expect(field("Restoration result").textContent).toBe(
        LABELS.restore[restore],
      );
      expect(field("Effective release after this run").textContent).toBe(
        restore === "verified" ? "v1.51.0" : "Unverified",
      );
      expect(
        field("Failed target (not automatically retried)").textContent,
      ).toBe("v1.52.0");
      expect(screen.getByTestId("host-updates-summary")).toHaveTextContent(
        mocks.query.automaticNotice.text,
      );
      expect(
        within(
          screen
            .getByRole("heading", { name: "Automation and maintenance" })
            .closest("section"),
        ).getByText(/Pause and disable prevent new automatic attempts/),
      ).toBeInTheDocument();
    },
  );
  it.each([
    ["verifying", [], /verifying readiness and stability/i],
    ["restored", ["migration_exceeds_window"], /^Notify-only/],
    ["restored", ["restore_not_certified"], /^Notify-only/],
  ])(
    "keeps current %s activity or refusal above restoration history %j",
    (state, reasons, expected) => {
      const data = restoreResponse();
      const snapshot = data.result.snapshot;
      Object.assign(snapshot.discovery, {
        state: reasons.length ? "blocked" : "available",
        candidate_version: "v1.53.0",
        reason_codes: reasons,
      });
      if (state === "verifying") {
        Object.assign(snapshot.operation, {
          state,
          kind: "apply",
          stage: "stability",
          phase: "target_verifying",
        });
        snapshot.safety.restore_state = "not_needed";
      }
      mocks.query.data = normalizeHostUpdateStatus(data);
      expect(mocks.query.data.availability).toBe("available");
      mocks.query.automaticNotice = automaticNoticeForStatus(mocks.query.data);
      const banner = notice();
      const bannerText = screen.getByRole("status").textContent;
      expect(bannerText).toMatch(expected);
      expect(bannerText).not.toMatch(
        /Update to v1\.52\.0 failed|Restore discards/,
      );
      banner.unmount();
      renderPage();
      const summary = screen.getByTestId("host-updates-summary");
      expect(summary).toHaveTextContent(mocks.query.automaticNotice.text);
      expect(summary).not.toHaveTextContent("Update to v1.52.0 failed");
      // The last run remains visible as history, without replacing the current summary.
      expect(field("Forward result").textContent).toBe("Failed");
      expect(field("Restoration result").textContent).toBe(
        "Cold backup and previous version verified",
      );
      expect(field("Effective release after this run").textContent).toBe(
        "v1.51.0",
      );
    },
  );
  it.each(projectedStatusFixtures)(
    "shows producer/reader $name in both notice and summary, retaining restoration only as history",
    ({ name, response }) => {
      mocks.query.data = normalizeHostUpdateStatus(response);
      expect(mocks.query.data).toEqual(response.result);
      mocks.query.automaticNotice = automaticNoticeForStatus(mocks.query.data);
      const expected =
        name === "verifying"
          ? /verifying readiness and stability/
          : /^Notify-only for this installation\./;
      const banner = notice();
      expect(screen.getByRole("status").textContent).toMatch(expected);
      expect(screen.getByRole("status")).not.toHaveTextContent(
        /Update to .* failed; restored/,
      );
      banner.unmount();
      renderPage();
      const summary = screen.getByTestId("host-updates-summary");
      expect(summary).toHaveTextContent(mocks.query.automaticNotice.text);
      expect(summary).not.toHaveTextContent(/Update to .* failed; restored/);
      expect(field("Restoration result").textContent).toBe(
        "Cold backup and previous version verified",
      );
      if (name !== "verifying") {
        expect(field("Last check result").textContent).toBe(
          LABELS.discovery.blocked,
        );
        expect(field("Operation (last reported)").textContent).toBe(
          LABELS.operation.blocked,
        );
      }
    },
  );
  it("has labels for every automatic phase, recovery and loss-window result", () => {
    const p = schema.properties;
    for (const [values, labels] of [
      [p.operation.properties.phase.anyOf[0].enum, LABELS.phase],
      [p.safety.properties.restore_state.enum, LABELS.restore],
      [p.safety.properties.fallback_strategy.enum, LABELS.strategy],
      [p.safety.properties.loss_window_qualification.enum, LABELS.loss_window],
      [p.last_run.anyOf[0].properties.forward_result.enum, LABELS.forward],
    ]) {
      expect(Object.keys(labels).sort()).toEqual([...values].sort());
    }
  });
  it("shows bounded snapshot/loss times and budgets rather than invented progress", () => {
    const s = mocks.query.data.snapshot;
    s.safety.estimates = Object.fromEntries(
      Object.keys(
        schema.properties.safety.properties.estimates.anyOf[0].properties,
      ).map((key) => [key, 120]),
    );
    s.safety.estimates.docker_required_free_bytes = 2147483648;
    s.discovery.reason_codes = ["migration_exceeds_window"];
    renderPage();
    expect(field("Snapshot taken at").querySelector("time")).toHaveAttribute(
      "datetime",
      s.safety.snapshot_at,
    );
    expect(field("Loss window start").querySelector("time")).toHaveAttribute(
      "datetime",
      s.safety.loss_window_start,
    );
    expect(field("Loss window end").querySelector("time")).toHaveAttribute(
      "datetime",
      s.safety.loss_window_end,
    );
    expect(field("Loss window qualification").textContent).toBe(
      "Bounded by the reported timestamps",
    );
    expect(field("Estimated migrations").textContent).toBe("2 min");
    expect(field("Required Docker free space").textContent).toBe("2.0 GiB");
    expect(
      screen.getByText(REASON_TEXT.migration_exceeds_window, {
        selector: "li",
      }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/\d+%/)).not.toBeInTheDocument();
  });
  it("marks stale restoration as historical and explains the automatic and manual boundaries", async () => {
    mocks.query.data.freshness = "stale";
    mocks.query.automaticNotice = automaticNoticeForStatus(mocks.query.data);
    renderPage();
    expect(screen.getByTestId("host-updates-summary")).toHaveTextContent(
      "current state unknown",
    );
    const user = userEvent.setup();
    await user.click(
      screen.getByRole("button", {
        name: "Automatic restoration is not image-only rollback",
      }),
    );
    expect(
      screen.getByText(
        /a failure after the cold backup restores that backup and the previous version automatically/,
      ),
    ).toBeVisible();
    expect(
      screen.getByText(
        /Legacy manual image rollback does not restore databases/,
      ),
    ).toBeVisible();
    expect(
      screen.queryByText(
        /Database restore is never automatic|does not automatically.*restore databases/,
      ),
    ).not.toBeInTheDocument();
  });
});

describe("quiet notice", () => {
  it.each(["loading", "forbidden", "unavailable", "unsupported", "current"])(
    "is absent for %s",
    (state) => {
      if (state === "loading") mocks.query.data = undefined;
      else if (state !== "current")
        mocks.query.data = { availability: state, snapshot: null };
      const { container } = notice();
      expect(container).toBeEmptyDOMElement();
    },
  );
  it.each(ACTIVE_STATES)("reports %s without invented progress", (state) => {
    mocks.query.data.snapshot.operation.state = state;
    mocks.query.data.progress_freshness = "fresh";
    notice();
    expect(screen.getByRole("status")).toHaveTextContent(
      `Last reported: ${LABELS.operation[state].toLowerCase()}`,
    );
    expect(screen.getByRole("status")).not.toHaveTextContent("%");
  });
  it.each(["failed", "interrupted", "blocked", "rolled_back"])(
    "requests attention for %s",
    (state) => {
      mocks.query.data.snapshot.operation.state = state;
      notice();
      expect(screen.getByRole("status")).toHaveTextContent(
        "host operator attention",
      );
    },
  );
  it("links a verified release to the details page with keyboard focus", async () => {
    mocks.query.data.snapshot.discovery.state = "available";
    const user = userEvent.setup();
    notice();
    const link = screen.getByRole("link", { name: "View update details" });
    expect(link).toHaveAttribute("href", "/dashboard/settings/updates");
    await user.tab();
    expect(link).toHaveFocus();
    expect(screen.getByRole("status")).toHaveAttribute("aria-live", "polite");
  });
  it("labels cached images downloaded, not installed", () => {
    mocks.query.data = statusFixture(
      "downloaded_independent_of_idle_operation",
    ).result;
    notice();
    expect(screen.getByRole("status")).toHaveTextContent(
      "downloaded, not installed",
    );
  });
  it("adds a neutral warning to stale relevant notices", () => {
    mocks.query.data.snapshot.discovery.state = "available";
    mocks.query.data.discovery_freshness = "stale";
    notice();
    expect(screen.getByRole("status")).toHaveTextContent(
      "current state is unknown",
    );
  });
});

describe("accessibility and safe reader actions", () => {
  it.each(["light", "dark"])(
    "uses semantic headings, fields and keyboard-accessible explanations in %s mode",
    async (mode) => {
      const user = userEvent.setup();
      renderPage(mode);
      expect(
        screen.getByRole("heading", { level: 1, name: "Updates" }),
      ).toBeInTheDocument();
      const refresh = screen.getByRole("button", { name: "Refresh report" });
      await user.tab();
      expect(refresh).toHaveFocus();
      expect(refresh).toHaveAccessibleDescription(
        "Rereads the latest host report. Does not start a release check or an update.",
      );
      const explainer = screen.getByRole("button", {
        name: "The host owns updates",
      });
      act(() => explainer.focus());
      await user.keyboard("{Enter}");
      expect(explainer).toHaveAttribute("aria-expanded", "true");
      const panel = document.getElementById(
        explainer.getAttribute("aria-controls"),
      );
      expect(
        within(panel).getByText(/This page only reads a report/),
      ).toBeVisible();
    },
  );
  it("refreshes only the report and prevents overlapping reads", async () => {
    const user = userEvent.setup();
    const page = renderPage();
    await user.click(screen.getByRole("button", { name: "Refresh report" }));
    expect(mocks.query.refresh).toHaveBeenCalledTimes(1);
    mocks.query.isFetching = true;
    page.rerender(<HostUpdatesPage />);
    expect(
      screen.getByRole("button", { name: "Reading report…" }),
    ).toBeDisabled();
  });
  it.each(["unresolved", "denied", "forbidden"])(
    "does not render operational data for %s access",
    (state) => {
      if (state === "unresolved") mocks.query.pending = true;
      if (state === "denied") mocks.query.allowed = false;
      if (state === "forbidden") mocks.query.data.availability = "forbidden";
      renderPage();
      expect(screen.queryByText("v1.2.3")).not.toBeInTheDocument();
      expect(screen.queryByRole("button")).not.toBeInTheDocument();
    },
  );
  it("shows a neutral loading state rather than default manual/current facts", () => {
    mocks.query.data = undefined;
    mocks.query.isLoading = true;
    mocks.query.isFetching = true;
    renderPage();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Reading the host report",
    );
    expect(
      screen.queryByRole("heading", { name: "Automation and maintenance" }),
    ).not.toBeInTheDocument();
  });
  it("renders dynamic values as text, never HTML or a supplied link", () => {
    mocks.query.data.snapshot.versions.selected_release =
      "<img src=x onerror=alert(1)>";
    const { container } = renderPage();
    expect(field("Selected release")).toHaveTextContent(
      "<img src=x onerror=alert(1)>",
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("a")).toBeNull();
  });
});
