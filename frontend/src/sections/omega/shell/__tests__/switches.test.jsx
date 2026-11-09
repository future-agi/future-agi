import React from "react";
import PropTypes from "prop-types";
import { render, screen } from "@testing-library/react";
import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CAPABILITIES_QUERY_KEY } from "src/hooks/useCapabilities";

import { OMEGA_PART, partCapabilityId } from "../rollout";
import {
  CODE_TAB,
  P22_SWITCHES,
  codeTabs,
  projectWords,
  switchoverSentence,
} from "../switches";
import { useOmegaPart } from "../useOmegaPart";
import { rolloutQueryClient } from "./rollout-fixtures";
import { ShellTestProviders } from "./test-providers";

// P22 PX-HIDE-SWITCHES: every switch ships off (hidden or honest wording)
// until its lane is accepted and the release declares its part. Each test
// below reads one switch in BOTH states.

const P22_PARTS = P22_SWITCHES.map((entry) => entry.part);

function Probe({ part }) {
  return <span data-testid="probe">{String(useOmegaPart(part))}</span>;
}

Probe.propTypes = { part: PropTypes.string.isRequired };

function readPart(client, part) {
  const view = render(
    <ShellTestProviders queryClient={client}>
      <Probe part={part} />
    </ShellTestProviders>,
  );
  const value = screen.getByTestId("probe").textContent;
  view.unmount();
  return value;
}

describe("the P22 registry", () => {
  beforeEach(() => vi.stubEnv("VITE_OMEGA_CONSOLE", "1"));
  afterEach(() => vi.unstubAllEnvs());

  it("names each switch once, with the lane that turns it on and where it shows", () => {
    expect(new Set(P22_PARTS).size).toBe(P22_PARTS.length);
    expect(P22_PARTS.sort()).toEqual(
      [
        OMEGA_PART.DEPLOY_NO_DOWNTIME,
        OMEGA_PART.DEPLOY_LANGGRAPH_JS,
        OMEGA_PART.SDK_PYTHON,
        OMEGA_PART.SDK_TYPESCRIPT,
      ].sort(),
    );
    for (const entry of P22_SWITCHES) {
      expect(entry.flippedBy).toMatch(/\S/);
      expect(entry.shows).toMatch(/\S/);
    }
  });

  it.each(P22_PARTS)(
    "%s is off with no release, on once the server declares it",
    (part) => {
      expect(readPart(rolloutQueryClient(), part)).toBe("false");
      expect(readPart(rolloutQueryClient({ parts: [part] }), part)).toBe(
        "true",
      );
      // Declaring another switch never turns this one on.
      const others = P22_PARTS.filter((other) => other !== part);
      expect(readPart(rolloutQueryClient({ parts: others }), part)).toBe(
        "false",
      );
    },
  );

  it("stays off with the Omega master switch off, whatever the server says", () => {
    vi.stubEnv("VITE_OMEGA_CONSOLE", "");
    for (const part of P22_PARTS) {
      expect(readPart(rolloutQueryClient({ parts: P22_PARTS }), part)).toBe(
        "false",
      );
    }
  });

  it("stays off while the answer loads and when the server is older", () => {
    const loading = new QueryClient({
      defaultOptions: { queries: { retry: false, enabled: false } },
    });
    expect(readPart(loading, OMEGA_PART.DEPLOY_NO_DOWNTIME)).toBe("false");
    const older = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    older.setQueryData(CAPABILITIES_QUERY_KEY, { data: { features: {} } });
    for (const part of P22_PARTS) expect(readPart(older, part)).toBe("false");
    const truthy = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    truthy.setQueryData(CAPABILITIES_QUERY_KEY, {
      data: {
        features: {
          [partCapabilityId(OMEGA_PART.DEPLOY_NO_DOWNTIME)]: { allowed: 1 },
        },
      },
    });
    expect(readPart(truthy, OMEGA_PART.DEPLOY_NO_DOWNTIME)).toBe("false");
  });
});

describe("Switch-over sentence (DEPLOY_NO_DOWNTIME, P19)", () => {
  it("off: about 3 seconds without service, naming the revision", () => {
    expect(switchoverSentence(false, 3)).toBe(
      "The agent is without service for about 3 seconds while revision 3 starts and answers its health check.",
    );
    expect(switchoverSentence(undefined)).toContain("the new revision");
    expect(switchoverSentence(false, 3)).not.toMatch(/keeps serving/);
  });

  it("on: the serving revision keeps serving, and stays if the next never answers", () => {
    const on = switchoverSentence(true, 4);
    expect(on).toBe(
      "The revision serving now keeps serving until revision 4 answers its health check, and stays live if it never does.",
    );
    expect(on).not.toMatch(/without service|seconds/);
  });
});

describe("Project wording (DEPLOY_LANGGRAPH_JS, P23)", () => {
  it("off: Omega projects in JS/TS only, and no LangGraph", () => {
    const off = projectWords(false);
    expect(off).toEqual({
      kind: "Omega projects (JS/TS)",
      one: "an Omega project (JS/TS)",
      folder: "a folder with omega.studio.json",
    });
    expect(JSON.stringify(off)).not.toMatch(/LangGraph|Python/);
  });

  it("on: Omega and LangGraph.js projects, still JS/TS", () => {
    const on = projectWords(true);
    expect(on.kind).toBe("Omega and LangGraph.js projects (JS/TS)");
    expect(on.one).toBe("an Omega or LangGraph.js project (JS/TS)");
    expect(on.folder).toBe("a folder with omega.studio.json or langgraph.json");
    expect(JSON.stringify(on)).not.toMatch(/Python/);
  });
});

describe("SDK tabs (SDK_PYTHON, SDK_TYPESCRIPT: publish)", () => {
  it("off: curl only", () => {
    expect(codeTabs()).toEqual([CODE_TAB.CURL]);
    expect(codeTabs({ python: false, typescript: false })).toEqual(["curl"]);
  });

  it("on: each SDK tab once its package is published", () => {
    expect(codeTabs({ python: true })).toEqual(["curl", "python"]);
    expect(codeTabs({ typescript: true })).toEqual(["curl", "typescript"]);
    expect(codeTabs({ python: true, typescript: true })).toEqual([
      "curl",
      "python",
      "typescript",
    ]);
  });
});
