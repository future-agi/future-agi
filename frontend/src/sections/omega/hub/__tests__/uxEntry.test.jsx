import React from "react";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";

import HubEntryPage from "../HubEntryPage";
import official from "../__fixtures__/entry-official.json";
import versions from "../__fixtures__/versions.json";
import me from "../__fixtures__/me-user.json";
import maintainer from "../__fixtures__/me-maintainer.json";
import rules from "../__fixtures__/rules.json";
import axios, { calls, respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));

const ENTRY = "/omega-hub/v1/entries/future-agi/first-agent";
const AT = "/dashboard/hub/future-agi/first-agent";
const routes = [
  ["get", "/api/capabilities/", capabilitiesAnswer({ parts: [] })],
  ["get", ENTRY, official],
  ["get", `${ENTRY}/versions`, versions],
  ["get", `${ENTRY}/remixes`, []],
  ["get", "/omega-hub/v1/me", me],
  ["get", "/omega-hub/v1/rules", rules],
  ["get", /^\/omega-hub\/v1\/publishers\//, official.publisher],
];
const page = (at = AT) =>
  renderAt(<HubEntryPage />, {
    path: "/dashboard/hub/:publisher/:name/:tab?",
    at,
  });

describe("minimal entry recovery", () => {
  it("replaces a missing pinned version's skeleton with an error and working latest link", async () => {
    respond([
      ...routes,
      [
        "get",
        `${ENTRY}/versions/99.0.0`,
        { error: { message: "Version not found" } },
        404,
      ],
    ]);
    page(`${AT}?version=99.0.0`);
    expect(
      await screen.findByText("This version isn't available"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("hub-error")).toBeInTheDocument();
    expect(screen.queryByTestId("hub-loading")).not.toBeInTheDocument();
    const latest = screen.getByRole("link", { name: "View latest version" });
    expect(latest).toHaveAttribute("href", AT);
    fireEvent.click(latest);
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(AT),
    );
    expect(screen.getByTestId("location")).not.toHaveTextContent("?version=");
    expect(
      await screen.findByTestId("hub-download-bundle"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("This version isn't available"),
    ).not.toBeInTheDocument();
  });

  it("retries a transient pinned-version failure without claiming the version is missing", async () => {
    respond([
      ...routes,
      [
        "get",
        `${ENTRY}/versions/0.9.0`,
        { error: { message: "Temporarily unavailable" } },
        500,
      ],
    ]);
    page(`${AT}?version=0.9.0`);
    expect(
      await screen.findByTestId("hub-error", {}, { timeout: 8000 }),
    ).toHaveTextContent("Temporarily unavailable");
    expect(
      screen.queryByText("This version isn't available"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "View latest version" }),
    ).not.toBeInTheDocument();
    respond([
      ...routes,
      [
        "get",
        `${ENTRY}/versions/0.9.0`,
        { ...official.latest, version: "0.9.0" },
      ],
    ]);
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByTestId("hub-download-bundle");
    expect(screen.queryByTestId("hub-error")).not.toBeInTheDocument();
  });

  it.each([404, 400])(
    "keeps Versions usable after pinned error %s and never offers an undefined yank",
    async (status) => {
      respond([
        ["get", "/omega-hub/v1/me", maintainer],
        ...routes,
        [
          "get",
          `${ENTRY}/versions/99.0.0`,
          { error: { message: "Pinned version failed" } },
          status,
        ],
      ]);
      page(`${AT}?version=99.0.0`);
      await screen.findByTestId("hub-error");
      // The maintainer actions sit under Maintainer in the More menu.
      fireEvent.click(screen.getByRole("button", { name: "More" }));
      expect(await screen.findByText("Maintainer")).toBeInTheDocument();
      expect(
        screen.getByRole("menuitem", { name: "Unrank" }),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("menuitem", { name: /Yank version/ }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("menuitem", { name: /Download/ }),
      ).not.toBeInTheDocument();
      expect(screen.queryByText(/undefined/)).not.toBeInTheDocument();
      fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
      await waitFor(() =>
        expect(screen.queryByRole("menu")).not.toBeInTheDocument(),
      );
      fireEvent.click(screen.getByTestId("hub-tab-versions"));
      expect(await screen.findByTestId("hub-versions")).toBeInTheDocument();
      expect(screen.queryByTestId("hub-error")).not.toBeInTheDocument();
    },
  );

  it("shows a removed pinned version without a futile retry", async () => {
    respond([
      ...routes,
      [
        "get",
        `${ENTRY}/versions/0.9.0`,
        { error: { message: "Version removed" } },
        410,
      ],
    ]);
    page(`${AT}?version=0.9.0`);
    expect(await screen.findByTestId("hub-error")).toHaveTextContent(
      "This version was removed",
    );
    expect(
      screen.queryByRole("button", { name: "Try again" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "View latest version" }));
    await screen.findByTestId("hub-download-bundle");
  });

  it("shows a skeleton rather than an empty Versions list while loading", async () => {
    respond(routes);
    const original = axios.get.getMockImplementation();
    let finish;
    const delayed = vi.spyOn(axios, "get").mockImplementation((url, config) =>
      url === `${ENTRY}/versions`
        ? new Promise((resolve) => {
            finish = resolve;
          })
        : original(url, config),
    );
    try {
      page(`${AT}/versions`);
      await screen.findByTestId("hub-entry");
      expect(finish).toBeTypeOf("function");
      expect(screen.getByTestId("hub-loading")).toBeInTheDocument();
      expect(
        screen.queryByText("No versions to show."),
      ).not.toBeInTheDocument();
      await act(async () => finish({ data: [], status: 200 }));
      expect(
        await screen.findByText("No versions to show."),
      ).toBeInTheDocument();
      expect(screen.queryByTestId("hub-loading")).not.toBeInTheDocument();
    } finally {
      if (finish) await act(async () => finish({ data: [], status: 200 }));
      delayed.mockImplementation(original);
    }
  });

  it("retries a failed Versions list instead of reporting it empty", async () => {
    respond([
      [
        "get",
        `${ENTRY}/versions`,
        { error: { message: "Versions unavailable" } },
        500,
      ],
      ...routes,
    ]);
    page(`${AT}/versions`);
    expect(
      await screen.findByTestId("hub-error", {}, { timeout: 8000 }),
    ).toHaveTextContent("Versions unavailable");
    expect(screen.queryByText("No versions to show.")).not.toBeInTheDocument();
    respond(routes);
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByTestId("hub-versions");
    expect(calls.some((call) => call.path === `${ENTRY}/versions`)).toBe(true);
    expect(screen.queryByTestId("hub-error")).not.toBeInTheDocument();
  });

  it("keeps a direct bundle URL and opens downloads in an isolated tab", async () => {
    respond(routes);
    page();
    const link = await screen.findByTestId("hub-download-bundle");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link.getAttribute("href")).toContain(
      `${ENTRY}/versions/${official.latest.version}/bundle?source=web`,
    );
    expect(calls.some((call) => call.path.endsWith("/bundle"))).toBe(false);
  });
});
