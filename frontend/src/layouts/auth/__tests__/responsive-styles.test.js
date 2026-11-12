import { describe, expect, it } from "vitest";
import { createTheme } from "@mui/material/styles";
import {
  authSplitSx,
  authFormPanelSx,
  authAsideSx,
  authFormGutterSx,
} from "../responsive-styles";
import loginSource from "src/sections/auth/jwt/jwt-login-view.jsx?raw";
import forgotSource from "src/sections/auth/jwt/forget-password-view.jsx?raw";
import resetSource from "src/sections/auth/jwt/jwt-password-reset-view.jsx?raw";
import ssoSource from "src/sections/auth/jwt/sso-login.jsx?raw";
import setupSource from "src/sections/auth/jwt/setup-org.jsx?raw";
import twoFactorSource from "src/sections/auth/jwt/two-factor-verify-view.jsx?raw";
import registerSource from "src/sections/auth/jwt/jwt-register-view.jsx?raw";
import orgRemovedSource from "src/sections/auth/jwt/org-removed.jsx?raw";

const theme = createTheme();

// CSS contract tests only. Pixel/scroll-width verification needs a browser.
function atWidth(sx, width) {
  const result = {};
  for (const [key, value] of Object.entries(theme.unstable_sx(sx))) {
    const media = key.match(/^@media \(min-width:(\d+)px\)$/);
    if (media) {
      if (width >= Number(media[1])) Object.assign(result, value);
    } else {
      result[key] = value;
    }
  }
  return result;
}

describe("auth view responsive styling", () => {
  it.each([
    [390, "column", "100%", "auto", "auto", "16px"],
    [768, "column", "100%", "auto", "auto", "32px"],
    [1280, "row", "50%", "100vh", "100%", "80px"],
    [1920, "row", "50%", "100vh", "100%", "80px"],
  ])(
    "resolves both visible panels and form gutters at %s px",
    (width, direction, panelWidth, formHeight, asideHeight, gutter) => {
      expect(atWidth(authSplitSx, width)).toMatchObject({
        display: "flex",
        width: "100%",
        minWidth: 0,
        flexDirection: direction,
        height: formHeight,
      });
      expect(atWidth(authFormPanelSx, width)).toMatchObject({
        width: panelWidth,
        minWidth: 0,
        height: formHeight,
      });
      expect(atWidth(authAsideSx, width)).toMatchObject({
        width: panelWidth,
        minWidth: 0,
        height: asideHeight,
        backgroundColor: "background.neutral",
      });
      expect(atWidth(authFormGutterSx, width)).toMatchObject({
        paddingLeft: gutter,
        paddingRight: gutter,
      });
    },
  );

  it.each([
    ["login (including invitation loading)", loginSource, 2],
    ["forgot password", forgotSource, 1],
    ["reset password", resetSource, 1],
    ["SSO", ssoSource, 1],
    ["organization setup", setupSource, 1],
    ["two-factor verification", twoFactorSource, 1],
    ["sign up", registerSource, 1],
    ["not part of any organization", orgRemovedSource, 1],
  ])("connects %s to the responsive panel styles", (_name, source, count) => {
    expect(source.match(/sx=\{authSplitSx\}|\.\.\.authSplitSx/g)).toHaveLength(
      count,
    );
    expect(source.match(/\.\.\.authFormPanelSx/g)).toHaveLength(count);
    expect(source.match(/sx=\{authAsideSx\}/g)).toHaveLength(count);
    expect(source).toContain("...authFormGutterSx");
  });

  // W10-AUTH-ORG-U04: a fixed 50/50 split with 80px gutters left about 35px
  // for the sign-up form on a 390px phone.
  it.each([
    ["sign up", registerSource],
    ["not part of any organization", orgRemovedSource],
  ])(
    "keeps no fixed half-width panel or 80px gutter on the %s page",
    (_name, source) => {
      expect(source).not.toMatch(/width:\s*"50%"/);
      expect(source).not.toMatch(/\bpx:\s*10\b/);
    },
  );

  it.each([
    ["forgot password", forgotSource],
    ["reset password", resetSource],
    ["SSO", ssoSource],
  ])("allows the %s form's 640px desktop width to shrink", (_name, source) => {
    expect(source).toContain('width: { xs: "100%", lg: "640px" }');
  });

  it("allows both 440px onboarding steps to shrink", () => {
    expect(
      setupSource.match(/width=\{\{ xs: "100%", lg: "440px" \}\}/g),
    ).toHaveLength(2);
  });
});
