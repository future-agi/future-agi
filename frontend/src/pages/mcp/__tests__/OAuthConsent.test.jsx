import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import axios from "src/utils/axios";
import OAuthConsent from "../OAuthConsent";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  endpoints: {
    mcp: {
      oauth: {
        authorize: "/mcp/oauth/authorize/",
        consent: "/mcp/oauth/consent/",
        approveInfo: "/mcp/oauth/approve-info/",
        approve: "/mcp/oauth/approve/",
      },
    },
  },
}));

const REQUEST_ID = "synthetic-request-0001";
const REDIRECT = "http://127.0.0.1:4567/callback";

// The approve-info contract: every group, ticked unless the editor asked for
// fewer (views/oauth_approve.py builds `checked` this way).
const groups = (checked = () => true) =>
  [
    { slug: "context", name: "Context & Navigation" },
    { slug: "evaluations", name: "Evaluations" },
    { slug: "usage", name: "Usage & Costs" },
  ].map((group) => ({
    ...group,
    description: `${group.name} tools`,
    checked: checked(group.slug),
  }));

const approveInfo = (availableGroups = groups()) => ({
  data: {
    status: true,
    result: {
      client_name: "Synthetic Editor",
      client_id: "synthetic-client",
      scopes: [],
      redirect_uri: REDIRECT,
      available_groups: availableGroups,
    },
  },
});

const redirectResponse = (url) => ({
  data: { status: true, result: { redirect_url: url } },
});

function mount(query) {
  return render(
    <MemoryRouter initialEntries={[`/mcp/authorize?${query}`]}>
      <OAuthConsent />
    </MemoryRouter>,
  );
}

const READ_ONLY_LINE =
  "This editor gets the tool groups that are switched on for you in Settings > MCP Server.";

let originalLocation;

beforeEach(() => {
  vi.clearAllMocks();
  originalLocation = Object.getOwnPropertyDescriptor(window, "location");
  // Approving ends in a full-page redirect to the editor; jsdom cannot navigate.
  Object.defineProperty(window, "location", {
    configurable: true,
    writable: true,
    value: { href: "http://localhost/mcp/authorize" },
  });
});

afterEach(() => {
  if (originalLocation) {
    Object.defineProperty(window, "location", originalLocation);
  }
});

describe("Editor sign-in page (request_id flow)", () => {
  it("shows a read-only line with a settings link instead of tick boxes", async () => {
    axios.get.mockResolvedValue(approveInfo());
    mount(`request_id=${REQUEST_ID}`);

    expect(await screen.findByText("Synthetic Editor")).toBeInTheDocument();
    expect(axios.get).toHaveBeenCalledWith(
      `/mcp/oauth/approve-info/?request_id=${REQUEST_ID}`,
    );

    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    expect(screen.queryByText(/Permissions \(/)).not.toBeInTheDocument();
    expect(screen.queryByText("Usage & Costs")).not.toBeInTheDocument();

    expect(screen.getByTestId("mcp-consent-tools-note")).toHaveTextContent(
      READ_ONLY_LINE,
    );
    const link = screen.getByRole("link", { name: "Settings > MCP Server" });
    expect(link).toHaveAttribute("href", "/dashboard/settings/mcp-server");
    // A new tab, so the pending approval on this page is not lost.
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("does not promise a revoke control", async () => {
    axios.get.mockResolvedValue(approveInfo());
    const { container } = mount(`request_id=${REQUEST_ID}`);
    await screen.findByText("Synthetic Editor");

    expect(container.textContent).not.toMatch(/revoke/i);
    expect(container.textContent).not.toMatch(/anytime/i);
  });

  it("approves with every group the server pre-selected, in the body the backend accepts", async () => {
    axios.get.mockResolvedValue(approveInfo());
    axios.post.mockResolvedValue(
      redirectResponse(`${REDIRECT}?code=synthetic-code&state=kept`),
    );
    mount(`request_id=${REQUEST_ID}`);
    await screen.findByText("Synthetic Editor");

    fireEvent.click(screen.getByRole("button", { name: "Authorize" }));

    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    expect(axios.post).toHaveBeenCalledWith("/mcp/oauth/approve/", {
      request_id: REQUEST_ID,
      approved: true,
      selected_groups: ["context", "evaluations", "usage"],
    });
    // Exactly the fields MCPOAuthApproveRequestSerializer accepts (it rejects
    // unknown fields).
    expect(Object.keys(axios.post.mock.calls[0][1]).sort()).toEqual([
      "approved",
      "request_id",
      "selected_groups",
    ]);
    await waitFor(() =>
      expect(window.location.href).toBe(
        `${REDIRECT}?code=synthetic-code&state=kept`,
      ),
    );
  });

  it("keeps sending only the groups the editor asked for when it asked for fewer", async () => {
    axios.get.mockResolvedValue(
      approveInfo(groups((slug) => slug !== "usage")),
    );
    axios.post.mockResolvedValue(redirectResponse(`${REDIRECT}?code=c`));
    mount(`request_id=${REQUEST_ID}`);
    await screen.findByText("Synthetic Editor");

    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Authorize" }));

    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith("/mcp/oauth/approve/", {
        request_id: REQUEST_ID,
        approved: true,
        selected_groups: ["context", "evaluations"],
      }),
    );
  });

  it("denies with the same body as before", async () => {
    axios.get.mockResolvedValue(approveInfo());
    axios.post.mockResolvedValue(
      redirectResponse(`${REDIRECT}?error=access_denied`),
    );
    mount(`request_id=${REQUEST_ID}`);
    await screen.findByText("Synthetic Editor");

    fireEvent.click(screen.getByRole("button", { name: "Deny" }));

    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith("/mcp/oauth/approve/", {
        request_id: REQUEST_ID,
        approved: false,
        selected_groups: [],
      }),
    );
  });
});

describe("Legacy client_id flow", () => {
  // In this flow the ticked groups do set the editor's tools: the token
  // exchange copies the code's scope into the person's tool groups. The ticks
  // stay; only the revoke promise goes.
  const legacyQuery =
    "client_id=synthetic-client&redirect_uri=http%3A%2F%2F127.0.0.1%3A4567%2Fcallback&response_type=code&state=kept";

  it("keeps the working tick boxes, drops the revoke promise and sends the ticked groups", async () => {
    axios.get.mockResolvedValue(approveInfo());
    axios.post.mockResolvedValue(redirectResponse(`${REDIRECT}?code=c`));
    const { container } = mount(legacyQuery);
    await screen.findByText("Synthetic Editor");

    const ticks = screen.getAllByRole("checkbox");
    expect(ticks).toHaveLength(3);
    expect(screen.queryByTestId("mcp-consent-tools-note")).toBeNull();
    expect(container.textContent).not.toMatch(/revoke/i);

    fireEvent.click(ticks[2]);
    fireEvent.click(screen.getByRole("button", { name: "Authorize" }));

    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith("/mcp/oauth/consent/", {
        client_id: "synthetic-client",
        redirect_uri: REDIRECT,
        state: "kept",
        approved: true,
        selected_groups: ["context", "evaluations"],
      }),
    );
  });
});

// The browser walk at 1280x720 found the card cut off partway through a
// 14-group list, with Deny and Authorize off screen and no way to scroll to
// them. jsdom does no layout, so these tests cannot show that the buttons are
// on screen at any size. They lock the structure and styles that keep them
// there: the card limits its height, Section's body box is the flex column
// that passes that limit down, the list scrolls inside it, and the header and
// the Deny/Authorize footer sit outside the list and never shrink. A browser
// re-walk proves the real fit.
describe("Sign-in card layout", () => {
  const legacyQuery =
    "client_id=synthetic-client&redirect_uri=http%3A%2F%2F127.0.0.1%3A4567%2Fcallback&response_type=code&state=kept";

  const fourteenGroups = () =>
    Array.from({ length: 14 }, (_, index) => ({
      slug: `group-${index + 1}`,
      name: `Tool group ${index + 1}`,
      description: `Tool group ${index + 1} tools`,
      checked: true,
    }));

  const expectActionsOutsideScrollingList = () => {
    const card = screen.getByTestId("mcp-consent-card");
    const scroll = screen.getByTestId("mcp-consent-scroll");
    const actions = screen.getByTestId("mcp-consent-actions");
    const deny = screen.getByRole("button", { name: "Deny" });
    const authorize = screen.getByRole("button", { name: "Authorize" });
    const title = screen.getByText("Authorize MCP Connection");

    // Deny and Authorize live in the footer, never inside the scrolling list.
    expect(actions).toContainElement(deny);
    expect(actions).toContainElement(authorize);
    expect(scroll).not.toContainElement(deny);
    expect(scroll).not.toContainElement(authorize);
    expect(scroll).not.toContainElement(actions);
    expect(scroll).not.toContainElement(title);

    // The card limits its height, clips, and lays its body out as a column.
    expect(card).toHaveStyle({
      display: "flex",
      flexDirection: "column",
      overflow: "hidden",
      maxHeight: "90dvh",
    });

    // Section's body box (the card's only child) holds the header, the list
    // and the footer, and may shrink to the card's limit.
    const body = scroll.parentElement;
    expect(body.parentElement).toBe(card);
    expect(actions.parentElement).toBe(body);
    expect(body).toHaveStyle({
      display: "flex",
      flexDirection: "column",
      minHeight: "0",
    });

    // The header and the footer keep their height; the list is the part that
    // gives way and scrolls.
    const header = body.firstElementChild;
    expect(header).toContainElement(title);
    expect(header).toHaveStyle({ flexShrink: "0" });
    expect(actions).toHaveStyle({ flexShrink: "0" });
    expect(scroll).toHaveStyle({ overflow: "auto", minHeight: "0" });

    return scroll;
  };

  it("keeps the tick-box list scrolling inside the card, above the Deny and Authorize footer (client_id flow)", async () => {
    axios.get.mockResolvedValue(approveInfo(fourteenGroups()));
    mount(legacyQuery);
    await screen.findByText("Synthetic Editor");

    const scroll = expectActionsOutsideScrollingList();
    expect(screen.getAllByRole("checkbox")).toHaveLength(14);
    expect(within(scroll).getAllByRole("checkbox")).toHaveLength(14);
    expect(
      within(scroll).getByText("Permissions (14 of 14)"),
    ).toBeInTheDocument();
  });

  it("keeps the read-only line in the scrolling area, above the Deny and Authorize footer (request_id flow)", async () => {
    axios.get.mockResolvedValue(approveInfo(fourteenGroups()));
    mount(`request_id=${REQUEST_ID}`);
    await screen.findByText("Synthetic Editor");

    const scroll = expectActionsOutsideScrollingList();
    expect(scroll).toContainElement(
      screen.getByTestId("mcp-consent-tools-note"),
    );
  });

  it("still sends the same body from the footer's Authorize button", async () => {
    axios.get.mockResolvedValue(approveInfo(fourteenGroups()));
    axios.post.mockResolvedValue(redirectResponse(`${REDIRECT}?code=c`));
    mount(legacyQuery);
    await screen.findByText("Synthetic Editor");

    const actions = screen.getByTestId("mcp-consent-actions");
    fireEvent.click(within(actions).getByRole("button", { name: "Authorize" }));

    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith("/mcp/oauth/consent/", {
        client_id: "synthetic-client",
        redirect_uri: REDIRECT,
        state: "kept",
        approved: true,
        selected_groups: fourteenGroups().map((group) => group.slug),
      }),
    );
  });
});

// The list scrolls inside the card, and many systems hide scrollbars until
// someone scrolls, so a list that ends exactly on a row looks complete. jsdom
// does no layout: these tests give the list the heights a browser would
// measure and check when the page says more lies below.
describe("More permissions below", () => {
  const legacyQuery =
    "client_id=synthetic-client&redirect_uri=http%3A%2F%2F127.0.0.1%3A4567%2Fcallback&response_type=code&state=kept";

  const fourteenGroups = () =>
    Array.from({ length: 14 }, (_, index) => ({
      slug: `group-${index + 1}`,
      name: `Tool group ${index + 1}`,
      description: `Tool group ${index + 1} tools`,
      checked: true,
    }));

  const layout = { scrollHeight: 0, clientHeight: 0 };
  const measured = {};

  // Only the scrolling list gets a size; every other element keeps jsdom's 0.
  const measure = (property) =>
    function size() {
      return this.dataset?.testid === "mcp-consent-scroll"
        ? layout[property]
        : 0;
    };

  beforeEach(() => {
    for (const property of ["scrollHeight", "clientHeight"]) {
      measured[property] = Object.getOwnPropertyDescriptor(
        HTMLElement.prototype,
        property,
      );
      Object.defineProperty(HTMLElement.prototype, property, {
        configurable: true,
        get: measure(property),
      });
    }
  });

  afterEach(() => {
    for (const property of ["scrollHeight", "clientHeight"]) {
      if (measured[property]) {
        Object.defineProperty(
          HTMLElement.prototype,
          property,
          measured[property],
        );
      } else {
        delete HTMLElement.prototype[property];
      }
    }
  });

  const scrollListTo = (scroll, top) => {
    Object.defineProperty(scroll, "scrollTop", {
      configurable: true,
      value: top,
    });
    fireEvent.scroll(scroll);
  };

  it("says more permissions lie below while the list runs past the card", async () => {
    // 14 rows in a list that shows about 6 of them.
    layout.scrollHeight = 980;
    layout.clientHeight = 420;
    axios.get.mockResolvedValue(approveInfo(fourteenGroups()));
    mount(legacyQuery);
    await screen.findByText("Synthetic Editor");

    const cue = await screen.findByTestId("mcp-consent-more");
    expect(cue).toHaveTextContent("More permissions below");
    // A visual cue only: a screen reader already reads the whole list and
    // the "Permissions (14 of 14)" count.
    expect(cue).toHaveAttribute("aria-hidden", "true");
    // It lies over the list's bottom edge, takes no room and lets clicks
    // through, so the list and the Deny and Authorize footer do not move.
    expect(cue).toHaveStyle({ pointerEvents: "none" });
    expect(cue.parentElement).toHaveStyle({ height: "0", flexShrink: "0" });
    expect(screen.getByTestId("mcp-consent-scroll")).not.toContainElement(cue);
    expect(screen.getByTestId("mcp-consent-actions")).not.toContainElement(cue);
  });

  it("drops the cue once the end of the list is in view, and shows it again when scrolled back up", async () => {
    layout.scrollHeight = 980;
    layout.clientHeight = 420;
    axios.get.mockResolvedValue(approveInfo(fourteenGroups()));
    mount(legacyQuery);
    await screen.findByText("Synthetic Editor");
    await screen.findByTestId("mcp-consent-more");

    const scroll = screen.getByTestId("mcp-consent-scroll");
    scrollListTo(scroll, 560);
    await waitFor(() =>
      expect(screen.queryByTestId("mcp-consent-more")).toBeNull(),
    );

    scrollListTo(scroll, 200);
    expect(await screen.findByTestId("mcp-consent-more")).toHaveTextContent(
      "More permissions below",
    );
  });

  it("shows no cue when every permission fits", async () => {
    layout.scrollHeight = 300;
    layout.clientHeight = 420;
    axios.get.mockResolvedValue(approveInfo());
    mount(legacyQuery);
    await screen.findByText("Synthetic Editor");

    expect(screen.getAllByRole("checkbox")).toHaveLength(3);
    expect(screen.queryByTestId("mcp-consent-more")).toBeNull();
  });
});
