import React from "react";
import { act, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";

import CopyField from "../components/CopyField";
import {
  keepAccessToken,
  resetAccessTokensForTests,
} from "../lib/accessTokens";
import { ID, detailView, renderDeployments } from "./helpers";
import { session, workspace } from "./session-mock";

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => workspace,
}));
vi.mock("src/sections/omega/shared", async (importOriginal) => ({
  ...(await importOriginal()),
  useOmegaSession: () => session,
}));

const { default: OverviewTab } = await import("../detail/OverviewTab");
const { default: SourceStep } = await import("../new/SourceStep");

// Keep this literal independent of the implementation so commit A runs at the
// reviewed head, before the copy constant moves to its own module in commit B.
const copyError =
  "Couldn't copy. If the token is hidden, choose Show. Select the token and copy it by hand.";
const token = "k".repeat(43);
const clipboardDescriptor = Object.getOwnPropertyDescriptor(
  navigator,
  "clipboard",
);

function installClipboard(failure) {
  const writeText = vi.fn(async () => {
    if (failure === "rejected write") throw new Error("Permission denied");
  });
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: failure === "missing clipboard" ? undefined : { writeText },
  });
  return writeText;
}

async function clickCopy(field, label) {
  // Flush the rejected promise before asserting absence. user-event would
  // install its own clipboard stub and hide the missing-API case.
  await act(async () => {
    fireEvent.click(
      within(field).getByRole("button", { name: `Copy ${label}` }),
    );
  });
}

function expectSilentFailure(field) {
  expect(within(field).queryByText(copyError)).toBeNull();
  expect(field.querySelector('[aria-live="polite"]')).toBeEmptyDOMElement();
  expect(field.querySelector("code")).not.toHaveStyle({ userSelect: "all" });
  expect(field.querySelector("code")).toHaveStyle({
    whiteSpace: "nowrap",
    overflow: "hidden",
    textOverflow: "ellipsis",
  });
}

beforeEach(() => {
  Object.assign(session, { state: "ready", code: null, message: null });
  resetAccessTokensForTests();
  vi.stubGlobal("isSecureContext", true);
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  if (clipboardDescriptor) {
    Object.defineProperty(navigator, "clipboard", clipboardDescriptor);
  } else {
    Reflect.deleteProperty(navigator, "clipboard");
  }
});

describe.each(["missing clipboard", "rejected write"])(
  "Copy failure scope with %s (C-FA09.failure)",
  (failure) => {
    it("keeps the overview URL silent but shows recovery for the access token", async () => {
      const writeText = installClipboard(failure);
      const deployment = detailView();
      keepAccessToken({ id: ID, accessToken: token });
      renderDeployments(<OverviewTab deployment={deployment} />);
      const field = await screen.findByTestId("overview-url");
      const accessToken = screen.getByTestId("access-token");
      expect(accessToken).not.toHaveTextContent(token);
      expect(
        accessToken.querySelector('[aria-live="polite"]'),
      ).toBeEmptyDOMElement();

      await clickCopy(field, "deployment URL");
      expectSilentFailure(field);
      expect(field).toHaveTextContent(deployment.url);
      expect(accessToken).not.toHaveTextContent(token);
      expect(within(accessToken).queryByText(copyError)).toBeNull();

      await clickCopy(accessToken, "access token");
      const error = within(accessToken).getByText(copyError);
      expect(error).toBeVisible();
      expect(error).toHaveStyle({ paddingBottom: "6px" });
      expect(accessToken.querySelector('[aria-live="polite"]')).toBe(error);
      expect(within(accessToken).getByText(token)).toHaveStyle({
        userSelect: "all",
        whiteSpace: "pre-wrap",
        overflowWrap: "anywhere",
      });
      expect(
        within(accessToken).getByRole("button", { name: "Hide" }),
      ).toBeInTheDocument();
      if (failure === "rejected write") {
        expect(writeText).toHaveBeenNthCalledWith(1, deployment.url);
        expect(writeText).toHaveBeenNthCalledWith(2, token);
      } else {
        expect(writeText).not.toHaveBeenCalled();
      }
      expectSilentFailure(field);

      // C-FA09.success stays generic even after a silent URL copy failure.
      const retry = installClipboard("success");
      await clickCopy(field, "deployment URL");
      expect(retry).toHaveBeenCalledWith(deployment.url);
      expect(field.querySelector('[aria-live="polite"]')).toHaveTextContent(
        "Copied",
      );
      expect(within(field).queryByText(copyError)).toBeNull();
    });

    it("keeps the CLI bundle command silent without the token recovery fallback", async () => {
      vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
      const writeText = installClipboard(failure);
      renderDeployments(<SourceStep onBundle={vi.fn()} />, {
        parts: [OMEGA_PART.DEPLOY_CLI],
      });
      fireEvent.click(await screen.findByTestId("source-cli"));
      const field = await screen.findByTestId("cli-bundle-command");
      const command = field.querySelector("code").textContent;
      await clickCopy(field, "bundle command");
      expectSilentFailure(field);
      expect(field.querySelector("code")).toHaveTextContent(command);
      expect(within(field).queryByRole("button", { name: "Show" })).toBeNull();
      if (failure === "rejected write") {
        expect(writeText).toHaveBeenCalledWith(command);
      } else {
        expect(writeText).not.toHaveBeenCalled();
      }

      const retry = installClipboard("success");
      await clickCopy(field, "bundle command");
      expect(retry).toHaveBeenCalledWith(command);
      expect(field.querySelector('[aria-live="polite"]')).toHaveTextContent(
        "Copied",
      );
      expect(within(field).queryByText(copyError)).toBeNull();
    });

    it("does not opt a generic masked field into access-token recovery", async () => {
      installClipboard(failure);
      renderDeployments(
        <CopyField
          value={token}
          label="value"
          secret
          testId="generic-secret"
        />,
      );
      const field = screen.getByTestId("generic-secret");
      await clickCopy(field, "value");
      expectSilentFailure(field);
      expect(field).not.toHaveTextContent(token);
      expect(
        within(field).getByRole("button", { name: "Show" }),
      ).toBeInTheDocument();
    });
  },
);
