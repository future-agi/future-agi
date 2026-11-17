// RED at 7e05e449: Studio/Console ignore admission and hasOpenPath only
// checks flags. Deploy status cases are controls preserving the accepted fix.
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  OMEGA_PRODUCT,
  PRODUCT_STATUS,
} from "src/sections/omega/shell/rollout";
import { useThisTargets as targetOptions } from "../lib/useThis";
import { hasOpenPath, showsOpens } from "../lib/installs";
import UseThisMenu from "../components/UseThisMenu";

const mock = vi.hoisted(() => ({ statuses: {}, consoleOn: true }));
vi.mock("src/sections/omega/shell/useOmegaProducts", () => ({
  useOmegaProductStatus: (p) => mock.statuses[p],
}));
vi.mock("src/sections/omega/shell/useOmegaPart", () => ({
  useOmegaPart: () => false,
}));
vi.mock("src/sections/omega/shell/flags", () => ({
  isOmegaConsoleEnabled: () => mock.consoleOn,
}));
const flags = { openInStudio: true, deploy: true, consoleTemplate: true };
const capabilities = { ...flags };
const all = {
  studioAdmitted: true,
  deploymentsAdmitted: true,
  consoleAdmitted: true,
  consoleEnabled: true,
};
const args = {
  ref: "future-agi/first-agent",
  version: "1.0.0",
  flags,
  capabilities,
};
const targets = [
  ["studio", "studioAdmitted", OMEGA_PRODUCT.STUDIO_TAB],
  ["deploy", "deploymentsAdmitted", OMEGA_PRODUCT.DEPLOYMENTS],
  ["console", "consoleAdmitted", OMEGA_PRODUCT.CONSOLE],
];
afterEach(() => {
  mock.statuses = {};
  mock.consoleOn = true;
});

describe("all receiving products must admit the target", () => {
  it("shows zero opens only with an admitted path and preserves real counts without one", () => {
    expect(hasOpenPath(flags, all)).toBe(true);
    expect(showsOpens(0, flags, all)).toBe(true);
    expect(showsOpens(0, flags)).toBe(false);
    expect(showsOpens(7, {}, {})).toBe(true);
  });
  it.each([undefined, "latest", "1.0.0-rc", "1.0.0\n"])(
    "never emits an unbounded or unpinned handoff (%s)",
    (version) => {
      expect(targetOptions({ ...args, ...all, version })).toEqual([]);
    },
  );
  it.each(targets)(
    "%s needs literal admission and matches opens-count availability",
    (key, field) => {
      for (const admitted of [
        false,
        undefined,
        "ADMITTED",
        "HIDDEN",
        "PAUSED",
        "LOADING",
        "UNAVAILABLE",
      ]) {
        const admission = { ...all, [field]: admitted };
        expect(
          targetOptions({ ...args, ...admission }).some((t) => t.key === key),
        ).toBe(false);
        const flag =
          key === "studio"
            ? "openInStudio"
            : key === "console"
              ? "consoleTemplate"
              : "deploy";
        const only = { [flag]: true };
        expect(hasOpenPath(only, admission)).toBe(false);
        expect(showsOpens(0, only, admission)).toBe(false);
        expect(showsOpens(7, only, admission)).toBe(true);
      }
      expect(
        targetOptions({ ...args, ...all }).some((t) => t.key === key),
      ).toBe(true);
    },
  );
  it.each(["studio", "console"])(
    "keeps %s off when the separate master gate is off",
    (key) => {
      expect(
        targetOptions({ ...args, ...all, consoleEnabled: false }).some(
          (t) => t.key === key,
        ),
      ).toBe(false);
      const only = {
        [key === "studio" ? "openInStudio" : "consoleTemplate"]: true,
      };
      expect(hasOpenPath(only, { ...all, consoleEnabled: false })).toBe(false);
      mock.consoleOn = false;
      mock.statuses[OMEGA_PRODUCT.STUDIO_TAB] = PRODUCT_STATUS.ADMITTED;
      mock.statuses[OMEGA_PRODUCT.CONSOLE] = PRODUCT_STATUS.ADMITTED;
      render(
        <MemoryRouter>
          <UseThisMenu
            entryRef={args.ref}
            version={args.version}
            flags={only}
            capabilities={capabilities}
          />
        </MemoryRouter>,
      );
      expect(screen.queryByTestId("hub-use-this")).toBeNull();
    },
  );
  it.each(targets)(
    "%s menu follows actual status hook, not listed/paused",
    (key, _, product) => {
      for (const status of [
        PRODUCT_STATUS.HIDDEN,
        PRODUCT_STATUS.PAUSED,
        PRODUCT_STATUS.LOADING,
        PRODUCT_STATUS.UNAVAILABLE,
        undefined,
      ]) {
        mock.statuses[product] = status;
        const view = render(
          <MemoryRouter>
            <UseThisMenu
              entryRef={args.ref}
              version={args.version}
              flags={flags}
              capabilities={capabilities}
            />
          </MemoryRouter>,
        );
        expect(screen.queryByTestId("hub-use-this")).toBeNull();
        view.unmount();
      }
      mock.statuses[product] = PRODUCT_STATUS.ADMITTED;
      render(
        <MemoryRouter>
          <UseThisMenu
            entryRef={args.ref}
            version={args.version}
            flags={flags}
            capabilities={capabilities}
          />
        </MemoryRouter>,
      );
      fireEvent.click(screen.getByTestId("hub-use-this-button"));
      expect(screen.getByTestId(`hub-use-this-${key}`)).toBeInTheDocument();
    },
  );
});
