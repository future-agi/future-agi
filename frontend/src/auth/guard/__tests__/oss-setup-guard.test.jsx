import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";

// TH-8084 option 1: every self-hosted install, licensed or not, gets the
// first-run checks before signup. Cloud never does.

const h = vi.hoisted(() => ({ mode: "oss", validated: false }));

vi.mock("react-router-dom", () => ({
  // eslint-disable-next-line react/prop-types
  Navigate: ({ to }) => <p>{`redirect:${to}`}</p>,
}));
vi.mock("src/routes/paths", () => ({ paths: { ossSetup: "/setup" } }));
vi.mock("src/components/loading-screen", () => ({ SplashScreen: () => null }));
vi.mock("src/sections/oss-first-run/ossFlowState", () => ({
  isValidationDone: () => h.validated,
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({
    mode: h.mode,
    isOSS: h.mode === "oss",
    isEE: h.mode === "ee",
    isCloud: h.mode === "cloud",
    isSelfHosted: h.mode !== "cloud",
    isLoading: false,
    isSuccess: true,
  }),
}));

import OssSetupGuard from "../oss-setup-guard";

beforeEach(() => {
  h.validated = false;
});

describe("OssSetupGuard", () => {
  it.each(["oss", "ee"])("%s: sends a fresh visitor to the checks", (mode) => {
    h.mode = mode;
    render(
      <OssSetupGuard>
        <p>signup</p>
      </OssSetupGuard>,
    );
    expect(screen.getByText("redirect:/setup")).toBeTruthy();
  });

  it("cloud: goes straight to signup", () => {
    h.mode = "cloud";
    render(
      <OssSetupGuard>
        <p>signup</p>
      </OssSetupGuard>,
    );
    expect(screen.getByText("signup")).toBeTruthy();
  });

  it("ee: after the checks, signup", () => {
    h.mode = "ee";
    h.validated = true;
    render(
      <OssSetupGuard>
        <p>signup</p>
      </OssSetupGuard>,
    );
    expect(screen.getByText("signup")).toBeTruthy();
  });
});
