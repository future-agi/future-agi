import React from "react";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import meUser from "../__fixtures__/me-user.json";
import HubBrowsePage from "../HubBrowsePage";
import HubVerifyEmailPage from "../HubVerifyEmailPage";
import CopyCommand from "../components/CopyCommand";
import LikeButton from "../components/LikeButton";
import ReportDialog from "../components/ReportDialog";
import { calls, respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));

const UNVERIFIED = {
  error: {
    code: "OMEGA_HUB_EMAIL_UNVERIFIED",
    message: "Confirm your email address first.",
    fix: "",
  },
};
const page = (results, sort) => ({
  count: results.length,
  next: null,
  previous: null,
  results,
  facets: { kinds: {} },
  sort,
});
const card = (ref) => ({
  ref,
  name: ref.split("/")[1],
  title: ref,
  tagline: "A tagline",
  kind: "agent",
  publisher: {
    handle: ref.split("/")[0],
    displayName: "Future AGI",
    tier: "official",
  },
  likes: 0,
  installs30d: 0,
});

describe("HUB-24: the browse page shows the order the server applied", () => {
  it("shows New in the sort when the server opened on New, and sends no sort itself", async () => {
    respond([
      [
        "get",
        "/omega-hub/v1/entries",
        page([card("future-agi/first-agent")], "new"),
      ],
      ["get", "/omega-hub/v1/picks", []],
    ]);
    renderAt(<HubBrowsePage />);
    await screen.findByTestId("hub-entry-card");
    const sort = () => screen.getByRole("combobox", { name: "Sort" });
    await waitFor(() => expect(sort()).toHaveTextContent("Sort: New"));
    expect(sort()).not.toHaveTextContent("Trending");
    const list = calls.find((call) => call.path === "/omega-hub/v1/entries");
    expect(list.params?.sort).toBeUndefined();
  });

  it("shows Trending in the sort once the server says it sorted by Trending", async () => {
    respond([
      [
        "get",
        "/omega-hub/v1/entries",
        page([card("future-agi/first-agent")], "trending"),
      ],
      ["get", "/omega-hub/v1/picks", []],
    ]);
    renderAt(<HubBrowsePage />);
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: "Sort" })).toHaveTextContent(
        "Sort: Trending",
      ),
    );
  });

  it("turns the sort off for a text search and says it is sorted by relevance", async () => {
    respond([
      [
        "get",
        "/omega-hub/v1/entries",
        page([card("future-agi/first-agent")], "relevance"),
      ],
      ["get", "/omega-hub/v1/picks", []],
    ]);
    renderAt(<HubBrowsePage />, { at: "/?q=first" });
    await screen.findByTestId("hub-entry-card");
    expect(screen.getByRole("combobox", { name: "Sort" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    // Most installed is offered only once the Hub CLI ships; none ships here.
    expect(screen.queryByTestId("hub-sort-installs")).toBeNull();
    expect(screen.getByText("Sorted by relevance")).toBeInTheDocument();
  });
});

// HUB-23 kept a long command's end by cutting its middle; the Hub now
// shows the whole command instead: it wraps, so its end (the entry and
// version) and everything before it stay visible (hubFirstVisit.test.jsx).
describe("HUB-23: a long command keeps its end", () => {
  it("shows the whole command, end included, and copies the full command", async () => {
    const text = "omega hub add hub:future-agi/first-agent@1.0.0";
    const long = `omega hub run ${"x".repeat(60)}`;
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    respond([]);
    renderAt(
      <>
        <CopyCommand text={text} testId="cmd" />
        <CopyCommand text={long} testId="long" />
      </>,
    );
    const code = screen.getByTestId("cmd").querySelector("code");
    expect(code).toHaveAttribute("title", text);
    expect(code.textContent).toBe(text);
    const longCode = screen.getByTestId("long").querySelector("code");
    expect(longCode.textContent).toBe(long);
    expect(longCode.textContent.endsWith("x".repeat(28))).toBe(true);
    expect(getComputedStyle(longCode).whiteSpace).toBe("pre-wrap");
    fireEvent.click(screen.getByRole("button", { name: `Copy ${text}` }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(text));
  });
});

describe("HUB-25: an unverified account is told how to confirm its email", () => {
  it("a refused like opens the notice, which mails the link", async () => {
    respond([
      [
        "post",
        "/omega-hub/v1/entries/future-agi/first-agent/like",
        UNVERIFIED,
        403,
      ],
      ["get", "/omega-hub/v1/me", { ...meUser, emailVerified: false }],
      [
        "post",
        "/omega-hub/v1/me/email-verification",
        { verified: false, sent: true, sentTo: meUser.user.email },
        202,
      ],
    ]);
    renderAt(
      <LikeButton
        publisher="future-agi"
        name="first-agent"
        likes={0}
        likedByMe={false}
        signedIn
      />,
    );
    fireEvent.click(screen.getByTestId("hub-like-button"));
    const notice = await screen.findByTestId("hub-email-verify-notice");
    expect(notice).toHaveTextContent("Confirm your email address first");
    fireEvent.click(screen.getByTestId("hub-email-verify-send"));
    await waitFor(() =>
      expect(notice).toHaveTextContent(
        `We sent a link to ${meUser.user.email}`,
      ),
    );
    expect(
      calls.filter(
        (call) => call.path === "/omega-hub/v1/me/email-verification",
      ),
    ).toHaveLength(1);
  });

  it("a refused report shows the notice inside the dialog", async () => {
    respond([
      ["get", "/omega-hub/v1/rules", { contacts: {} }],
      ["post", "/omega-hub/v1/reports", UNVERIFIED, 403],
      ["get", "/omega-hub/v1/me", { ...meUser, emailVerified: false }],
    ]);
    renderAt(
      <ReportDialog
        open
        onClose={() => {}}
        entryRef="future-agi/first-agent"
      />,
    );
    fireEvent.change(screen.getByTestId("hub-report-details"), {
      target: { value: "It crashes on start every time." },
    });
    fireEvent.click(screen.getByRole("button", { name: /send report/i }));
    expect(
      await screen.findByTestId("hub-email-verify-notice"),
    ).toHaveTextContent("send a report");
  });

  it("the link page confirms once and says so", async () => {
    respond([
      [
        "post",
        "/omega-hub/v1/me/email-verification/confirm",
        { verified: true },
      ],
    ]);
    renderAt(<HubVerifyEmailPage />, { at: "/?token=abc.def" });
    expect(await screen.findByTestId("hub-verify-done")).toHaveTextContent(
      "Your email is confirmed",
    );
    const confirms = calls.filter(
      (call) => call.path === "/omega-hub/v1/me/email-verification/confirm",
    );
    expect(confirms).toEqual([
      expect.objectContaining({ data: { token: "abc.def" } }),
    ]);
  });

  it("an expired link offers a new one", async () => {
    respond([
      [
        "post",
        "/omega-hub/v1/me/email-verification/confirm",
        {
          error: {
            code: "OMEGA_HUB_EMAIL_LINK_EXPIRED",
            message: "This confirmation link has expired.",
            fix: "",
          },
        },
        400,
      ],
      [
        "post",
        "/omega-hub/v1/me/email-verification",
        { verified: false, sent: true, sentTo: "c@example.com" },
        202,
      ],
    ]);
    renderAt(<HubVerifyEmailPage />, { at: "/?token=old" });
    expect(await screen.findByTestId("hub-verify-failed")).toHaveTextContent(
      "This link has expired",
    );
    fireEvent.click(screen.getByTestId("hub-verify-resend"));
    await waitFor(() =>
      expect(screen.getByTestId("hub-verify-failed")).toHaveTextContent(
        "A new link is on its way to c@example.com",
      ),
    );
  });

  it("a link without a token says what to do and calls nothing", async () => {
    respond([]);
    renderAt(<HubVerifyEmailPage />, { at: "/" });
    expect(await screen.findByTestId("hub-verify-missing")).toBeInTheDocument();
    expect(calls).toHaveLength(0);
  });
});
