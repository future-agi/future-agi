import React from "react";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  OMEGA_PART,
  OMEGA_PRODUCT,
  OMEGA_PRODUCTS,
  PRODUCT_STATUS,
  capabilityIdFor,
  productStatus,
} from "src/sections/omega/shell/rollout";
import { useOmegaProductStatus } from "src/sections/omega/shell/useOmegaProducts";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";

import { useThisTargets as targetsFor } from "../lib/useThis";
// These legacy tests isolate Deploy's gate; Studio/Console admission is explicit.
const useThisTargets = (args) =>
  targetsFor({
    studioAdmitted: true,
    consoleAdmitted: true,
    consoleEnabled: true,
    ...args,
  });
import UseThisMenu from "../components/UseThisMenu";
import axiosMock, { respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));
vi.mock("src/config-global", () => ({ HOST_API: "https://api.futureagi.com" }));

const ALL = {
  cli: true,
  openInStudio: true,
  deploy: true,
  consoleTemplate: true,
};
const NONE = {
  cli: true,
  openInStudio: false,
  deploy: false,
  consoleTemplate: false,
};
// `cli`: a customer can install a CLI with `omega hub` (OMEGA_PART HUB_CLI).
const ON = {
  cli: true,
  openInStudio: true,
  deploy: true,
  consoleTemplate: true,
};
const OFF = {
  cli: true,
  openInStudio: false,
  deploy: false,
  consoleTemplate: false,
};
const NO_CLI = { ...OFF, cli: false };
const keys = (targets) => targets.map((t) => t.key);

describe("Use this targets (SPEC §7.4)", () => {
  it("shows only the CLI item when every capability is false", () => {
    expect(
      keys(
        useThisTargets({
          ref: "aa/bbb",
          version: "1.0.0",
          capabilities: NONE,
          flags: ON,
        }),
      ),
    ).toEqual(["cli"]);
  });

  it("shows only the CLI item when every Vite flag is unset", () => {
    expect(
      keys(
        useThisTargets({
          ref: "aa/bbb",
          version: "1.0.0",
          capabilities: ALL,
          flags: OFF,
        }),
      ),
    ).toEqual(["cli"]);
  });

  it("shows Studio only when both the capability and the flag are true", () => {
    const studioOnlyCap = { ...NONE, openInStudio: true };
    expect(
      keys(
        useThisTargets({
          ref: "aa/bbb",
          version: "1.0.0",
          capabilities: studioOnlyCap,
          flags: OFF,
        }),
      ),
    ).toEqual(["cli"]);
    expect(
      keys(
        useThisTargets({
          ref: "aa/bbb",
          version: "1.0.0",
          capabilities: NONE,
          flags: { ...OFF, openInStudio: true },
        }),
      ),
    ).toEqual(["cli"]);
    const both = useThisTargets({
      ref: "aa/bbb",
      version: "1.0.0",
      capabilities: studioOnlyCap,
      flags: { ...OFF, openInStudio: true },
    });
    expect(keys(both)).toEqual(["cli", "studio"]);
    expect(both[1].href).toBe("/dashboard/studio?hub=aa%2Fbbb%401.0.0");
  });

  it("offers no CLI item until the CLI ships `omega hub`, and nothing at all with no handoff", () => {
    expect(
      keys(
        useThisTargets({
          ref: "aa/bbb",
          version: "1.0.0",
          capabilities: ALL,
          flags: NO_CLI,
        }),
      ),
    ).toEqual([]);
    expect(
      keys(
        useThisTargets({
          ref: "aa/bbb",
          version: "1.0.0",
          capabilities: ALL,
          flags: { ...ON, cli: false },
          deploymentsAdmitted: true,
        }),
      ),
    ).toEqual(["studio", "deploy", "console"]);
    // An older caller that passes no `cli` flag gets no CLI item (fail closed).
    expect(
      keys(
        useThisTargets({
          ref: "aa/bbb",
          version: "1.0.0",
          capabilities: ALL,
          flags: { deploy: true },
          deploymentsAdmitted: true,
        }),
      ),
    ).toEqual(["deploy"]);
  });

  it("draws no Use this section when there is nothing to offer", () => {
    renderAt(
      <UseThisMenu
        entryRef="future-agi/first-agent"
        version="1.0.0"
        capabilities={ALL}
        flags={NO_CLI}
      />,
    );
    expect(screen.queryByTestId("hub-use-this")).toBeNull();
    expect(screen.queryByTestId("hub-cli-add")).toBeNull();
  });

  it("pins the install command to the version and hands off with the pinned reference", () => {
    const targets = useThisTargets({
      ref: "c-test/csv-answers",
      version: "1.0.1",
      capabilities: ALL,
      flags: ON,
      deploymentsAdmitted: true,
    });
    expect(targets[0].commands).toEqual({
      add: "omega hub add hub:c-test/csv-answers@1.0.1",
      run: "omega hub run hub:c-test/csv-answers@1.0.1",
    });
    expect(keys(targets)).toEqual(["cli", "studio", "deploy", "console"]);
    expect(targets.find((t) => t.key === "deploy").href).toBe(
      "/dashboard/deployments/new?hub=c-test%2Fcsv-answers%401.0.1",
    );
  });

  it("renders the menu with the CLI item alone when the env flags are unset", () => {
    renderAt(
      <UseThisMenu
        entryRef="future-agi/first-agent"
        version="1.0.0"
        capabilities={ALL}
        flags={{ cli: true }}
      />,
    );
    expect(screen.getByTestId("hub-cli-add")).toHaveTextContent(
      "omega hub add hub:future-agi/first-agent@1.0.0",
    );
    fireEvent.click(screen.getByTestId("hub-use-this-button"));
    expect(screen.getByTestId("hub-use-this-cli")).toBeInTheDocument();
    expect(screen.queryByTestId("hub-use-this-studio")).toBeNull();
    expect(screen.queryByTestId("hub-use-this-deploy")).toBeNull();
    expect(screen.queryByTestId("hub-use-this-console")).toBeNull();
  });
});

// RL 1399: Deploy opens /dashboard/deployments/new, which shell/ProductGate.jsx
// renders only when the server admits Deployments (anything else there is a
// 404, the paused page or a retry page). Hosted Future AGI withholds
// Deployments (omega_rollout/service.py _hosted_withheld), so the Hub offers
// Deploy only when the Deployments status is ADMITTED, the version's
// capability says deploy and the Vite flag is on; never while the answer loads.
const DEPLOYMENTS = OMEGA_PRODUCT.DEPLOYMENTS;
const withoutDeploy = (targets) => targets.filter((t) => t.key !== "deploy");
const args = (overrides = {}) => ({
  ref: "aa/bbb",
  version: "1.0.0",
  capabilities: ALL,
  flags: ON,
  ...overrides,
});

describe("Deploy is offered only where Deployments is admitted (RL 1399)", () => {
  it("offers no Deploy when Deployments is not admitted, and none by default", () => {
    expect(keys(useThisTargets(args({ deploymentsAdmitted: false })))).toEqual([
      "cli",
      "studio",
      "console",
    ]);
    expect(keys(useThisTargets(args()))).toEqual(["cli", "studio", "console"]);
    // Only `true` admits: the status string itself, or any other value, does not.
    expect(
      keys(
        useThisTargets(args({ deploymentsAdmitted: PRODUCT_STATUS.ADMITTED })),
      ),
    ).toEqual(["cli", "studio", "console"]);
  });

  it("offers Deploy with the pinned reference when Deployments is admitted", () => {
    const targets = useThisTargets(args({ deploymentsAdmitted: true }));
    expect(keys(targets)).toEqual(["cli", "studio", "deploy", "console"]);
    expect(targets.find((t) => t.key === "deploy").href).toBe(
      "/dashboard/deployments/new?hub=aa%2Fbbb%401.0.0",
    );
  });

  it("still needs the Vite flag and the version's capability when admitted", () => {
    expect(
      keys(
        useThisTargets(
          args({ deploymentsAdmitted: true, flags: { ...ON, deploy: false } }),
        ),
      ),
    ).toEqual(["cli", "studio", "console"]);
    expect(
      keys(
        useThisTargets(
          args({
            deploymentsAdmitted: true,
            capabilities: { ...ALL, deploy: false },
          }),
        ),
      ),
    ).toEqual(["cli", "studio", "console"]);
    expect(
      keys(
        useThisTargets(
          args({
            deploymentsAdmitted: true,
            capabilities: { openInStudio: true, consoleTemplate: true },
          }),
        ),
      ),
    ).toEqual(["cli", "studio", "console"]);
  });

  it("leaves every other target as it was, admitted or not", () => {
    const capabilityCases = [
      ALL,
      NONE,
      { ...ALL, deploy: false },
      {},
      undefined,
    ];
    const flagCases = [
      ON,
      OFF,
      NO_CLI,
      { ...ON, deploy: false },
      { ...ON, cli: false },
      { deploy: true },
    ];
    const cases = capabilityCases.flatMap((capabilities) =>
      flagCases.map((flags) => ({ capabilities, flags })),
    );
    expect(cases).toHaveLength(30);
    cases.forEach(({ capabilities, flags }) => {
      const admitted = useThisTargets(
        args({ capabilities, flags, deploymentsAdmitted: true }),
      );
      const notAdmitted = useThisTargets(
        args({ capabilities, flags, deploymentsAdmitted: false }),
      );
      expect(withoutDeploy(notAdmitted)).toEqual(withoutDeploy(admitted));
    });
    const others = withoutDeploy(
      useThisTargets(args({ deploymentsAdmitted: false })),
    );
    expect(others.find((t) => t.key === "studio").href).toBe(
      "/dashboard/studio?hub=aa%2Fbbb%401.0.0",
    );
    expect(others.find((t) => t.key === "console").href).toBe(
      "/dashboard/console?hub=aa%2Fbbb%401.0.0",
    );
  });
});

// The menu reads the Deployments status from the same cached
// /api/capabilities/ answer ProductGate reads. A probe next to the menu shows
// that status, so every "no Deploy" below is asserted after the answer is in
// (or while it provably is not), never vacuously during loading.
function DeploymentsStatusProbe() {
  return (
    <span data-testid="deployments-status">
      {useOmegaProductStatus(DEPLOYMENTS)}
    </span>
  );
}

const CAPABILITIES = "/api/capabilities/";
const HANDOFFS = { openInStudio: true, deploy: true, consoleTemplate: true };
const DEPLOY_HREF =
  "/dashboard/deployments/new?hub=future-agi%2Ffirst-agent%401.0.0";
const LOADED = { isLoading: false, isError: false };

function answerWithDeployments(feature) {
  const answer = structuredClone(
    capabilitiesAnswer({ parts: [OMEGA_PART.HUB_CLI] }),
  );
  const id = capabilityIdFor(DEPLOYMENTS);
  if (feature === undefined) delete answer.features[id];
  else answer.features[id] = { ...answer.features[id], ...feature };
  return answer;
}

const VENUES = {
  hostedWithheld: capabilitiesAnswer({
    admitted: OMEGA_PRODUCTS.filter((product) => product !== DEPLOYMENTS),
    parts: [OMEGA_PART.HUB_CLI],
  }),
  noDeploymentsEntry: answerWithDeployments(undefined),
  paused: capabilitiesAnswer({
    paused: [DEPLOYMENTS],
    parts: [OMEGA_PART.HUB_CLI],
  }),
  resolverUnavailable: answerWithDeployments({
    allowed: false,
    reason_code: "RESOLVER_UNAVAILABLE",
  }),
  selfHostAdmitted: capabilitiesAnswer({ parts: [OMEGA_PART.HUB_CLI] }),
};

function renderMenu({ capabilities = ALL, flags = HANDOFFS } = {}) {
  return renderAt(
    <>
      <UseThisMenu
        entryRef="future-agi/first-agent"
        version="1.0.0"
        capabilities={capabilities}
        flags={flags}
      />
      <DeploymentsStatusProbe />
    </>,
  );
}

const statusShown = () => screen.getByTestId("deployments-status").textContent;
const openMenu = () =>
  fireEvent.click(screen.getByTestId("hub-use-this-button"));
function expectOtherTargets({ cli }) {
  expect(screen.getByTestId("hub-use-this-studio")).toBeInTheDocument();
  expect(screen.getByTestId("hub-use-this-console")).toBeInTheDocument();
  if (cli) expect(screen.getByTestId("hub-use-this-cli")).toBeInTheDocument();
  else expect(screen.queryByTestId("hub-use-this-cli")).toBeNull();
}

describe("Use this menu: Deploy follows the Deployments admission (RL 1399)", () => {
  beforeEach(() => vi.stubEnv("VITE_OMEGA_CONSOLE", "1"));
  afterEach(() => {
    respond([]);
    vi.unstubAllEnvs();
  });

  it.each([
    [
      "hosted Future AGI withholds Deployments",
      "hostedWithheld",
      PRODUCT_STATUS.HIDDEN,
    ],
    [
      "the answer has no Deployments entry",
      "noDeploymentsEntry",
      PRODUCT_STATUS.HIDDEN,
    ],
    ["Deployments is paused", "paused", PRODUCT_STATUS.PAUSED],
    [
      "the rollout resolver is unavailable",
      "resolverUnavailable",
      PRODUCT_STATUS.UNAVAILABLE,
    ],
  ])("offers no Deploy when %s", async (_, venue, status) => {
    const answer = VENUES[venue];
    // The fixture is the venue it names (rollout.js productStatus).
    expect(productStatus(answer, DEPLOYMENTS, LOADED)).toBe(status);
    respond([["get", CAPABILITIES, answer]]);
    renderMenu();
    // Positive controls: the answer is in (the status and the Hub CLI part).
    await waitFor(() => expect(statusShown()).toBe(status));
    expect(await screen.findByTestId("hub-cli-add")).toBeInTheDocument();
    openMenu();
    expectOtherTargets({ cli: true });
    expect(screen.queryByTestId("hub-use-this-deploy")).toBeNull();
  });

  it("offers no Deploy when the rollout answer cannot be read", async () => {
    respond([["get", CAPABILITIES, { error: { code: "TEST_DOWN" } }, 500]]);
    renderMenu();
    await waitFor(
      () => expect(statusShown()).toBe(PRODUCT_STATUS.UNAVAILABLE),
      {
        timeout: 10000,
      },
    );
    expect(screen.queryByTestId("hub-use-this")).toBeNull();
    expect(screen.queryByTestId("hub-use-this-deploy")).toBeNull();
  });

  it("offers no Deploy while the rollout answer loads", async () => {
    let asked = false;
    axiosMock.get.mockImplementationOnce((url) => {
      asked = String(url).includes(CAPABILITIES);
      return new Promise(() => {});
    });
    renderMenu();
    await waitFor(() => expect(asked).toBe(true));
    expect(statusShown()).toBe(PRODUCT_STATUS.LOADING);
    expect(screen.queryByTestId("hub-use-this")).toBeNull();
    expect(screen.queryByTestId("hub-use-this-deploy")).toBeNull();
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(statusShown()).toBe(PRODUCT_STATUS.LOADING);
    expect(screen.queryByTestId("hub-use-this-deploy")).toBeNull();
  });

  it("draws Deploy only once the answer admits Deployments (no flash while it loads)", async () => {
    let settle;
    axiosMock.get.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          settle = resolve;
        }),
    );
    renderMenu();
    await waitFor(() => expect(settle).toBeTypeOf("function"));
    expect(screen.queryByTestId("hub-use-this")).toBeNull();
    expect(statusShown()).toBe(PRODUCT_STATUS.LOADING);
    expect(screen.queryByTestId("hub-use-this-deploy")).toBeNull();
    await act(async () => {
      settle({ data: VENUES.selfHostAdmitted, status: 200 });
    });
    await waitFor(() => expect(statusShown()).toBe(PRODUCT_STATUS.ADMITTED));
    openMenu();
    expect(
      await screen.findByTestId("hub-use-this-deploy"),
    ).toBeInTheDocument();
    expectOtherTargets({ cli: true });
  });

  it("offers Deploy on a self-hosted install that admits Deployments, with the pinned reference", async () => {
    expect(productStatus(VENUES.selfHostAdmitted, DEPLOYMENTS, LOADED)).toBe(
      PRODUCT_STATUS.ADMITTED,
    );
    respond([["get", CAPABILITIES, VENUES.selfHostAdmitted]]);
    renderMenu();
    await waitFor(() => expect(statusShown()).toBe(PRODUCT_STATUS.ADMITTED));
    expect(await screen.findByTestId("hub-cli-add")).toBeInTheDocument();
    openMenu();
    expectOtherTargets({ cli: true });
    fireEvent.click(screen.getByTestId("hub-use-this-deploy"));
    await waitFor(() =>
      expect(screen.getByTestId("location").textContent).toBe(DEPLOY_HREF),
    );
  });

  it.each([
    ["the Vite flag is off", { flags: { ...HANDOFFS, deploy: false } }],
    ["the version cannot deploy", { capabilities: { ...ALL, deploy: false } }],
  ])(
    "offers no Deploy when Deployments is admitted but %s",
    async (_, props) => {
      respond([["get", CAPABILITIES, VENUES.selfHostAdmitted]]);
      renderMenu(props);
      await waitFor(() => expect(statusShown()).toBe(PRODUCT_STATUS.ADMITTED));
      expect(await screen.findByTestId("hub-cli-add")).toBeInTheDocument();
      openMenu();
      expectOtherTargets({ cli: true });
      expect(screen.queryByTestId("hub-use-this-deploy")).toBeNull();
    },
  );
});
