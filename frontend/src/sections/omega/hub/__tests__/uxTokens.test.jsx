import React from "react";
import {
  act,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TokenSettings from "../components/TokenSettings";
import axios, { calls, respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));

const TOKENS = "/omega-hub/v1/me/tokens";
const tokens = ["laptop", "build worker"].map((name, i) => ({
  id: `token-${i}`,
  name,
  publisher: "test-publisher",
  prefix: `oh_test_${i}`,
  expiresAt: "2026-12-01T00:00:00Z",
  revokedAt: null,
}));
let listed;
const row = (name) => screen.getByText(name).closest("li");
const deletions = () => calls.filter((call) => call.method === "delete");

beforeEach(() => {
  listed = [...tokens];
  respond([
    ["get", TOKENS, () => listed],
    ["delete", `${TOKENS}/token-0`, {}],
  ]);
});

async function openRevoke(name = "laptop") {
  renderAt(<TokenSettings publishers={[]} />);
  await screen.findByText(name);
  fireEvent.click(
    within(row(name)).getByRole("button", { name: "Revoke", exact: true }),
  );
  return screen.getByRole("dialog");
}

describe("HUB-UX-05 token revocation", () => {
  it("confirms the token name and immediate CLI consequence, and cancels without deleting", async () => {
    const dialog = await openRevoke();
    expect(dialog).toHaveTextContent("Revoke laptop?");
    expect(dialog).toHaveTextContent(
      "The CLI cannot use this token for new publishing requests.",
    );
    expect(
      within(dialog).getByRole("button", { name: "Revoke token" }),
    ).toHaveClass("MuiButton-containedError");
    expect(deletions()).toHaveLength(0);
    expect(screen.queryByText("Token revoked")).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(deletions()).toHaveLength(0);
    expect(row("laptop")).toBeInTheDocument();
  });

  it("announces success only after a confirmed successful response and disables only its pending row", async () => {
    let finish;
    axios.delete.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const dialog = await openRevoke();
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Revoke token" }),
    );
    await waitFor(() => expect(finish).toBeTypeOf("function"));
    // The modal owns interaction while pending, but list pending state is per row.
    await waitFor(() =>
      expect(
        within(row("laptop")).getByText("Revoke").closest("button"),
      ).toBeDisabled(),
    );
    expect(
      within(row("build worker")).getByText("Revoke").closest("button"),
    ).toBeEnabled();
    expect(screen.queryByText("Token revoked")).not.toBeInTheDocument();
    expect(row("laptop")).toBeInTheDocument();
    listed = [tokens[1]];
    await act(async () => finish({ data: {}, status: 204 }));
    expect(await screen.findByText("Token revoked")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByText("laptop")).not.toBeInTheDocument(),
    );
    expect(row("build worker")).toBeInTheDocument();
  });

  it("renders a failed revoke under the list, never announces success and never removes the token", async () => {
    let fail;
    axios.delete.mockImplementationOnce(
      () =>
        new Promise((_, reject) => {
          fail = reject;
        }),
    );
    const dialog = await openRevoke();
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Revoke token" }),
    );
    await waitFor(() => expect(fail).toBeTypeOf("function"));
    expect(screen.queryByText("Token revoked")).not.toBeInTheDocument();
    expect(row("laptop")).toBeInTheDocument();
    await act(async () =>
      fail({
        statusCode: 403,
        error: { message: "Revocation refused. Token is still active." },
      }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    const error = await screen.findByRole("alert");
    expect(error).toHaveTextContent(
      "Revocation refused. Token is still active.",
    );
    expect(
      screen.getByRole("list").compareDocumentPosition(error) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(screen.queryByText("Token revoked")).not.toBeInTheDocument();
    expect(
      within(row("laptop")).getByRole("button", {
        name: "Revoke",
        exact: true,
      }),
    ).toBeEnabled();
    expect(row("build worker")).toBeInTheDocument();
  });

  it("clears an earlier success before a later failed revoke", async () => {
    const dialog = await openRevoke();
    listed = [tokens[1]];
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Revoke token" }),
    );
    await screen.findByText("Token revoked");
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    respond([
      ["get", TOKENS, () => listed],
      [
        "delete",
        `${TOKENS}/token-1`,
        { error: { message: "Second revoke failed" } },
        400,
      ],
    ]);
    fireEvent.click(
      within(row("build worker")).getByRole("button", {
        name: "Revoke",
        exact: true,
      }),
    );
    expect(screen.queryByText("Token revoked")).not.toBeInTheDocument();
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Revoke token",
      }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Second revoke failed",
    );
    expect(screen.queryByText("Token revoked")).not.toBeInTheDocument();
    expect(row("build worker")).toBeInTheDocument();
  });
});
