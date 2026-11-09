/* eslint-disable react/prop-types */
// L3: the dataset Experiments tab body and header DevelopDetailView mounts.
// Prototype -> DSX leaderboard; non-prototype -> classic list and header,
// including a build with VITE_DSX_EXPERIMENTS_BASIC explicitly false.
import React from "react";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  experimentsBasicFromEnv,
  setLaunchOverrideForTest,
} from "src/launch/launchSwitch";

const mode = vi.hoisted(() => ({ prototype: true }));
const mounts = vi.hoisted(() => ({
  dsxTab: 0,
  dsxHeader: 0,
  tab: 0,
  header: 0,
  refs: [],
}));

vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => mode.prototype,
}));
vi.mock("../../ExperimentTab/ExperimentTab", async () => {
  const R = await import("react");
  return {
    default: R.forwardRef((props, ref) => {
      mounts.dsxTab += 1;
      mounts.refs.push(ref);
      return <div data-testid="dsx-leaderboard" />;
    }),
  };
});
vi.mock("../../ExperimentTab/ExperimentTabHeader", () => ({
  default: () => {
    mounts.dsxHeader += 1;
    return <div data-testid="dsx-header" />;
  },
}));
vi.mock("../ExperimentTab", async () => {
  const R = await import("react");
  return {
    default: R.forwardRef((props, ref) => {
      mounts.tab += 1;
      mounts.refs.push(ref);
      return <div data-testid="classic-list" />;
    }),
  };
});
vi.mock("../ExperimentTabHeader", () => ({
  default: () => {
    mounts.header += 1;
    return <div data-testid="classic-header" />;
  },
}));

import ExperimentTabSwitch from "../ExperimentTabSwitch";
import ExperimentTabHeaderSwitch from "../ExperimentTabHeaderSwitch";

const reset = () => {
  Object.assign(mounts, { dsxTab: 0, dsxHeader: 0, tab: 0, header: 0 });
  mounts.refs = [];
};

const both = () => {
  const ref = React.createRef();
  render(
    <>
      <ExperimentTabHeaderSwitch tabRef={ref} rowSelected={[]} />
      <ExperimentTabSwitch ref={ref} setRowSelected={() => {}} />
    </>,
  );
  return ref;
};

describe("L3 Experiments tab switch", () => {
  afterEach(() => {
    setLaunchOverrideForTest(null);
    reset();
  });

  it("prototype mode keeps the DSX leaderboard and header", () => {
    mode.prototype = true;
    setLaunchOverrideForTest({ launch: true, experimentsBasic: true });
    const ref = both();
    expect(screen.getByTestId("dsx-leaderboard")).toBeTruthy();
    expect(screen.getByTestId("dsx-header")).toBeTruthy();
    expect(mounts.tab + mounts.header).toBe(0);
    expect(mounts.refs).toContain(ref);
  });

  it("non-prototype + experimentsBasic mounts dev's list and header, never the leaderboard", () => {
    mode.prototype = false;
    setLaunchOverrideForTest({ launch: true, experimentsBasic: true });
    const ref = both();
    expect(screen.getByTestId("classic-list")).toBeTruthy();
    expect(screen.getByTestId("classic-header")).toBeTruthy();
    expect(mounts.dsxTab + mounts.dsxHeader).toBe(0);
    expect(mounts.refs).toContain(ref);
  });

  // D2 / J09 REVISED: explicit OFF gates NEW surfaces, never the classic list.
  it("non-prototype with an explicit false build input mounts the classic list and header", () => {
    mode.prototype = false;
    const experimentsBasic = experimentsBasicFromEnv({
      VITE_DSX_EXPERIMENTS_BASIC: "false",
    });
    expect(experimentsBasic).toBe(false);
    setLaunchOverrideForTest({ launch: true, experimentsBasic });
    both();
    expect(screen.getByTestId("classic-list")).toBeVisible();
    expect(screen.getByTestId("classic-header")).toBeVisible();
    expect(mounts.dsxTab + mounts.dsxHeader).toBe(0);
  });
});
