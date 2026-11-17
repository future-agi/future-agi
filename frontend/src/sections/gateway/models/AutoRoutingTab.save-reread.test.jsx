import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import AutoRoutingTab from "./AutoRoutingTab";
import { CATALOG_MODELS } from "./test-fixtures/catalog";

// The browser stops waiting for update-config at 30 s; the server does not,
// so a save reported as timed out (or failed) can still have been stored.
// After an error the page re-reads the config and shows what is stored: a
// stored change clears "Unsaved changes", edits that were not stored stay.
// The error toast is still shown.

const { post, get, snackbars, config, configRead } = vi.hoisted(() => ({
  post: vi.fn(),
  get: vi.fn(),
  snackbars: [],
  config: { current: {} },
  // hold: a promise the next config read waits on; fail: reject it once.
  configRead: { hold: null, fail: false },
}));

vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org-one" }),
}));

vi.mock("src/utils/axios", () => ({
  default: { post, get },
  endpoints: {
    gateway: {
      config: (id) => `/gateway/${id}/config`,
      updateConfig: (id) => `/gateway/${id}/config/update`,
      providers: (id) => `/gateway/${id}/providers`,
    },
  },
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: (message, options) => snackbars.push({ message, options }),
}));

const CONFIG_URL = "/gateway/gw-1/config";

const timedOut = () =>
  Object.assign(new Error("timeout of 30000ms exceeded"), {
    code: "ECONNABORTED",
  });

const renderTab = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          mutationCache: new MutationCache({ onError: () => {} }),
          defaultOptions: {
            mutations: { retry: false },
            queries: { retry: false },
          },
        })
      }
    >
      <AutoRoutingTab
        gatewayId="gw-1"
        canWrite
        catalogModels={CATALOG_MODELS}
        managedStatus={{ available: true, enabled: true }}
        baseUrl="https://gw.example.com/v1"
      />
    </QueryClientProvider>,
  );

const configReads = () =>
  get.mock.calls.filter(([url]) => url === CONFIG_URL).length;
const preference = (name) =>
  screen.getByRole("button", { name: new RegExp(`^${name}`) });
const saveButton = () => screen.getByRole("button", { name: /Save changes/ });

// The server stores the patch, then the request fails in the browser.
const storeThenFail = (error) => async (_url, body) => {
  config.current = {
    routing: { auto: { ...config.current.routing.auto, ...body.routing.auto } },
  };
  throw error;
};

const editToCost = async () => {
  await waitFor(() =>
    expect(preference("Balanced")).toHaveAttribute("aria-pressed", "true"),
  );
  await userEvent.click(preference("Cost"));
  expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
  return configReads();
};

beforeEach(() => {
  post.mockReset();
  get.mockReset();
  snackbars.length = 0;
  config.current = { routing: { auto: { preference: "balanced" } } };
  configRead.hold = null;
  configRead.fail = false;
  get.mockImplementation(async (url) => {
    if (url.endsWith("/providers")) {
      return {
        data: {
          status: true,
          result: { providers: [{ id: "own", models: ["gpt-4o"] }] },
        },
      };
    }
    if (configRead.hold) await configRead.hold;
    if (configRead.fail) {
      configRead.fail = false;
      throw timedOut();
    }
    return { data: { status: true, result: config.current } };
  });
});

describe("AutoRoutingTab: a save that errors re-reads the config", () => {
  it("shows a timed-out save that was stored as saved, after the timeout toast", async () => {
    let finishRead;
    post.mockImplementationOnce(async (url, body) => {
      configRead.hold = new Promise((resolve) => {
        finishRead = resolve;
      });
      return storeThenFail(timedOut())(url, body);
    });
    renderTab();
    const readsBefore = await editToCost();

    await userEvent.click(saveButton());

    await waitFor(() =>
      expect(snackbars).toContainEqual({
        message: expect.stringMatching(/^Saving auto routing timed out/),
        options: { variant: "error" },
      }),
    );
    // The toast does not wait for the re-read.
    await waitFor(() => expect(configReads()).toBeGreaterThan(readsBefore));
    expect(saveButton()).toBeEnabled();
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();

    configRead.hold = null;
    finishRead();

    await waitFor(() =>
      expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument(),
    );
    expect(preference("Cost")).toHaveAttribute("aria-pressed", "true");
    expect(saveButton()).toBeDisabled();
    expect(snackbars).not.toContainEqual(
      expect.objectContaining({ options: { variant: "success" } }),
    );
  });

  it("shows a failed save that was stored as saved, and still reports the failure", async () => {
    post.mockImplementationOnce(
      storeThenFail(
        Object.assign(new Error("Request failed"), { statusCode: 502 }),
      ),
    );
    renderTab();
    const readsBefore = await editToCost();

    await userEvent.click(saveButton());

    await waitFor(() =>
      expect(snackbars).toContainEqual(
        expect.objectContaining({ options: { variant: "error" } }),
      ),
    );
    await waitFor(() => expect(configReads()).toBeGreaterThan(readsBefore));
    await waitFor(() =>
      expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument(),
    );
    expect(preference("Cost")).toHaveAttribute("aria-pressed", "true");
    expect(snackbars).not.toContainEqual(
      expect.objectContaining({ options: { variant: "success" } }),
    );
  });

  it("keeps the edits when the re-read shows the save was not stored", async () => {
    post.mockRejectedValueOnce(timedOut());
    renderTab();
    const readsBefore = await editToCost();

    await userEvent.click(saveButton());

    await waitFor(() =>
      expect(snackbars).toContainEqual({
        message: expect.stringMatching(/^Saving auto routing timed out/),
        options: { variant: "error" },
      }),
    );
    await waitFor(() => expect(configReads()).toBeGreaterThan(readsBefore));
    await waitFor(() => expect(saveButton()).toBeEnabled());
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    expect(preference("Cost")).toHaveAttribute("aria-pressed", "true");
    expect(config.current).toEqual({
      routing: { auto: { preference: "balanced" } },
    });
  });

  it("says the settings could not be read when the re-read fails too, and Retry brings back the edits", async () => {
    post.mockImplementationOnce(async () => {
      configRead.fail = true;
      throw timedOut();
    });
    renderTab();
    const readsBefore = await editToCost();

    await userEvent.click(saveButton());

    await waitFor(() => expect(configReads()).toBeGreaterThan(readsBefore));
    expect(
      await screen.findByText("Couldn't load the routing settings"),
    ).toBeInTheDocument();
    expect(snackbars).toContainEqual({
      message: expect.stringMatching(/^Saving auto routing timed out/),
      options: { variant: "error" },
    });

    await userEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() =>
      expect(preference("Cost")).toHaveAttribute("aria-pressed", "true"),
    );
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    expect(saveButton()).toBeEnabled();
  });
});
