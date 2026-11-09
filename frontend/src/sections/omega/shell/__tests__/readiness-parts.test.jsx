import React from "react";
import PropTypes from "prop-types";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { render, screen } from "@testing-library/react";
import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CAPABILITIES_QUERY_KEY } from "src/hooks/useCapabilities";

import PartGate from "../PartGate";
import { OMEGA_PART, isPartReady, partCapabilityId } from "../rollout";
import { useOmegaPart } from "../useOmegaPart";
import { capabilitiesAnswer } from "./rollout-answer";
import { rolloutQueryClient } from "./rollout-fixtures";
import { ShellTestProviders } from "./test-providers";

// HIDE-UNFINISHED-FA: a part of a product that does not work today (a CLI
// command no customer can install, a deployment that cannot open in Studio) is
// hidden, never shown as "soon". The server decides (omega_rollout/readiness.py)
// and reports `omega_part_<part>`; the browser fails closed on anything but
// `allowed: true`. One part is a configuration error, not an unfinished
// control: a Hub with no checker keeps its Publish page, which says what is
// missing (main-session ruling 3; hub/__tests__/uploadsUnavailable.test.jsx).
// PartGate below is the gate's own contract, shown with that part's id.

const SETTLED = { isLoading: false, isError: false };

function Probe({ part }) {
  return <span data-testid="probe">{String(useOmegaPart(part))}</span>;
}

Probe.propTypes = { part: PropTypes.string.isRequired };

function renderWith(client, ui, path = "/") {
  return render(
    <ShellTestProviders queryClient={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/404"
            element={<span data-testid="host-404">404</span>}
          />
          <Route path="*" element={ui} />
        </Routes>
      </MemoryRouter>
    </ShellTestProviders>,
  );
}

describe("isPartReady", () => {
  it("is true only for an explicit allowed answer", () => {
    const answer = capabilitiesAnswer({ parts: [OMEGA_PART.DEPLOY_CLI] });
    expect(isPartReady(answer, OMEGA_PART.DEPLOY_CLI, SETTLED)).toBe(true);
    for (const part of [
      OMEGA_PART.HUB_CLI,
      OMEGA_PART.HUB_PUBLISH,
      OMEGA_PART.DEPLOYMENT_STUDIO,
    ]) {
      expect(isPartReady(answer, part, SETTLED)).toBe(false);
    }
    // An older backend without the entry, no answer, loading, an error.
    expect(isPartReady({ features: {} }, OMEGA_PART.HUB_CLI, SETTLED)).toBe(
      false,
    );
    expect(isPartReady(undefined, OMEGA_PART.HUB_CLI, SETTLED)).toBe(false);
    expect(
      isPartReady(answer, OMEGA_PART.DEPLOY_CLI, {
        isLoading: true,
        isError: false,
      }),
    ).toBe(false);
    expect(
      isPartReady(answer, OMEGA_PART.DEPLOY_CLI, {
        isLoading: false,
        isError: true,
      }),
    ).toBe(false);
    const truthyNotTrue = {
      features: { [partCapabilityId(OMEGA_PART.HUB_CLI)]: { allowed: "yes" } },
    };
    expect(isPartReady(truthyNotTrue, OMEGA_PART.HUB_CLI, SETTLED)).toBe(false);
  });

  it("names the server's capability ids", () => {
    expect(Object.values(OMEGA_PART).map(partCapabilityId)).toEqual([
      "omega_part_hub_cli",
      "omega_part_hub_publish",
      "omega_part_deploy_cli",
      "omega_part_deployment_studio",
      // The parity program's hide switches (switches.js, readiness.py SWITCH_PARTS).
      "omega_part_deploy_no_downtime",
      "omega_part_deploy_langgraph_js",
      "omega_part_sdk_python",
      "omega_part_sdk_typescript",
    ]);
  });
});

describe("useOmegaPart and PartGate", () => {
  beforeEach(() => vi.stubEnv("VITE_OMEGA_CONSOLE", "1"));
  afterEach(() => vi.unstubAllEnvs());

  it("reads the cached capabilities answer", () => {
    renderWith(
      rolloutQueryClient({ parts: [OMEGA_PART.HUB_CLI] }),
      <>
        <Probe part={OMEGA_PART.HUB_CLI} />
      </>,
    );
    expect(screen.getByTestId("probe")).toHaveTextContent("true");
  });

  it("is false with the master switch off, whatever the server says", () => {
    vi.stubEnv("VITE_OMEGA_CONSOLE", "");
    renderWith(
      rolloutQueryClient({ parts: Object.values(OMEGA_PART) }),
      <Probe part={OMEGA_PART.HUB_CLI} />,
    );
    expect(screen.getByTestId("probe")).toHaveTextContent("false");
  });

  it("renders the page only for a part that works, else the host's 404", () => {
    const page = (
      <PartGate part={OMEGA_PART.HUB_PUBLISH}>
        <span data-testid="page">publish</span>
      </PartGate>
    );
    renderWith(rolloutQueryClient({ parts: [OMEGA_PART.HUB_PUBLISH] }), page);
    expect(screen.getByTestId("page")).toBeInTheDocument();
  });

  it("sends a part that does not work to the 404 route", () => {
    renderWith(
      rolloutQueryClient({ parts: [] }),
      <PartGate part={OMEGA_PART.HUB_PUBLISH}>
        <span data-testid="page">publish</span>
      </PartGate>,
      "/dashboard/hub/publish",
    );
    expect(screen.queryByTestId("page")).not.toBeInTheDocument();
    expect(screen.getByTestId("host-404")).toBeInTheDocument();
  });

  it("shows the loading screen, never the page, while the answer loads", () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    // A query that never settles: the gate must not render its page.
    client.setQueryDefaults(CAPABILITIES_QUERY_KEY, {
      queryFn: () => new Promise(() => {}),
    });
    renderWith(
      client,
      <PartGate part={OMEGA_PART.HUB_PUBLISH}>
        <span data-testid="page">publish</span>
      </PartGate>,
    );
    expect(screen.queryByTestId("page")).not.toBeInTheDocument();
    expect(screen.queryByTestId("host-404")).not.toBeInTheDocument();
  });
});
