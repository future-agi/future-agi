// The Row view's API copy items need both dataset.rowApiPath and a generated
// GET. The default-off surface omits it; positive copy tests simulate a future
// ON surface. Copy id and Copy link always stay.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { OPENAPI_CONTRACT } from "src/api/contracts/openapi-contract.generated";
import * as apiSurface from "src/api/contracts/api-surface";
import { FI_BASE_URL } from "src/sections/common/commands/requestSnippets";

const { Null } = vi.hoisted(() => ({ Null: () => null }));
vi.mock("src/components/iconify", () => ({ default: Null }));
vi.mock("src/sections/common/ux/toast", () => ({ notify: () => {} }));

import RowIdChip from "../RowIdChip";
import {
  ROW_API_SURFACE,
  ROW_READ_TEMPLATE,
  rowApiCopyProps,
  rowReadRequest,
} from "../rowApiPath";

const theme = createTheme({ palette: palette("light") });
const DATASET = "8f7c3c4e-0a5b-4c55-9a59-3f3b9e2f3a01";
const ROW = "2d4e6f80-91a2-4b3c-8d5e-6f708192a3b4";
const PATH = `/model-hub/datasets/${DATASET}/rows/${ROW}/`;

const launch = (madeReal = []) =>
  setLaunchOverrideForTest({ launch: true, experimentsBasic: false, madeReal });

let writes;
beforeEach(() => {
  writes = [];
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: {
      writeText: async (text) => {
        writes.push(text);
      },
    },
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  setLaunchOverrideForTest(null);
});

const enableRowRead = () =>
  vi.spyOn(apiSurface, "getContractedApiMethods").mockReturnValue(["get"]);

const renderChip = (link) =>
  render(
    <ThemeProvider theme={theme}>
      <RowIdChip datasetId={DATASET} rowId={ROW} link={link} />
    </ThemeProvider>,
  );
// While a menu is open MUI hides its siblings from the accessibility tree.
const chip = () =>
  screen.getByRole("button", { name: `Copy ID ${ROW}`, hidden: true });
const openMenu = () => fireEvent.contextMenu(chip());

describe("the row read in the generated contract", () => {
  it("omits the row read while its server flag is OFF", () => {
    expect(OPENAPI_CONTRACT.endpoints[ROW_READ_TEMPLATE]).toBeUndefined();
    expect(apiSurface.getContractedApiMethods(ROW_READ_TEMPLATE)).toEqual([]);
    expect(rowReadRequest(DATASET, ROW)).toBeNull();
  });

  it("builds the same method and path when the GET becomes available", () => {
    enableRowRead();
    expect(rowReadRequest(DATASET, ROW)).toEqual({ method: "GET", path: PATH });
    expect(rowReadRequest(DATASET, "a/b")).toEqual({
      method: "GET",
      path: `/model-hub/datasets/${DATASET}/rows/a%2Fb/`,
    });
    expect(rowReadRequest(DATASET, null)).toBeNull();
    expect(rowReadRequest(undefined, ROW)).toBeNull();
  });
});

describe("rowApiCopyProps", () => {
  it("offers nothing while dataset.rowApiPath is hidden", () => {
    launch();
    expect(rowApiCopyProps(DATASET, ROW)).toEqual({});
    // A sibling Row view claim does not reveal it.
    launch(["dataset.rowExtras.runs"]);
    expect(rowApiCopyProps(DATASET, ROW)).toEqual({});
  });

  it("offers the path and a command with key placeholders when GET is available", () => {
    enableRowRead();
    launch([ROW_API_SURFACE]);
    const { apiPath, apiCall } = rowApiCopyProps(DATASET, ROW);
    expect(apiPath).toBe(`GET ${PATH}`);
    expect(apiCall).toBe(
      "# Create a personal API key pair at /dashboard/keys\n" +
        [
          `curl -X GET "${FI_BASE_URL}${PATH}"`,
          '-H "X-Api-Key: $FI_API_KEY"',
          '-H "X-Secret-Key: $FI_SECRET_KEY"',
        ].join(" \\\n  "),
    );
    // Auth names the person's own key variables, never a value.
    const headers = apiCall.split("\n").filter((line) => line.includes("-H"));
    expect(headers).toHaveLength(2);
    for (const line of headers)
      expect(line).toMatch(/-H "X-(Api|Secret)-Key: \$FI_[A-Z_]+"( \\)?$/);
    expect(apiCall).not.toMatch(/Bearer|Authorization|sk-|pk-/);
  });
});

describe("Row view id chip", () => {
  it.each([true, false])(
    "keeps API items absent with the server OFF (launch=%s)",
    async (isLaunch) => {
      setLaunchOverrideForTest({
        launch: isLaunch,
        madeReal: [ROW_API_SURFACE],
      });
      renderChip("http://app.test/row-link");
      openMenu();
      expect(
        screen.queryByRole("menuitem", { name: "Copy API path" }),
      ).toBeNull();
      expect(
        screen.queryByRole("menuitem", { name: "Copy as API call" }),
      ).toBeNull();
      fireEvent.click(screen.getByRole("menuitem", { name: "Copy id" }));
      await waitFor(() => expect(writes).toEqual([ROW]));
      fireEvent.click(chip(), { shiftKey: true });
      await waitFor(() => expect(writes).toEqual([ROW, ROW]));
    },
  );

  it("keeps id and link copy but no API items while unclaimed", async () => {
    launch();
    renderChip("http://app.test/row-link");
    openMenu();
    expect(
      screen.getByRole("menuitem", { name: "Copy id" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("menuitem", { name: "Copy link" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("menuitem", { name: "Copy API path" }),
    ).toBeNull();
    expect(
      screen.queryByRole("menuitem", { name: "Copy as API call" }),
    ).toBeNull();
    fireEvent.click(screen.getByRole("menuitem", { name: "Copy id" }));
    await waitFor(() => expect(writes).toEqual([ROW]));
    openMenu();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Copy link" }));
    await waitFor(() =>
      expect(writes).toEqual([ROW, "http://app.test/row-link"]),
    );
  });

  it("copies the path and the API call only with the claim and GET", async () => {
    enableRowRead();
    launch([ROW_API_SURFACE]);
    renderChip("http://app.test/row-link");
    openMenu();
    expect(
      screen.getByRole("menuitem", { name: "Copy id" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("menuitem", { name: "Copy link" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: "Copy API path" }));
    await waitFor(() => expect(writes).toEqual([`GET ${PATH}`]));
    openMenu();
    fireEvent.click(
      await screen.findByRole("menuitem", { name: "Copy as API call" }),
    );
    await waitFor(() => expect(writes).toHaveLength(2));
    expect(writes[1]).toBe(rowApiCopyProps(DATASET, ROW).apiCall);
    expect(writes[1]).toContain(`"${FI_BASE_URL}${PATH}"`);
  });

  it("Shift+click copies the API path only with the claim and GET", async () => {
    enableRowRead();
    launch();
    renderChip();
    fireEvent.click(chip(), { shiftKey: true });
    await waitFor(() => expect(writes).toEqual([ROW]));
    cleanup();
    launch([ROW_API_SURFACE]);
    renderChip();
    fireEvent.click(chip(), { shiftKey: true });
    await waitFor(() => expect(writes).toEqual([ROW, `GET ${PATH}`]));
  });
});
