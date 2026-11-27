import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";

let result;
const asked = [];
vi.mock("src/api/simulate-environments/runDetail", async (importOriginal) => ({
  ...(await importOriginal()),
  useCallBrowser: (...args) => {
    asked.push(args);
    return result;
  },
}));

const { default: TrialBrowserPanel } = await import("../TrialBrowserPanel");
const { TRIAL_BROWSER_COPY: C, linkable } = await import(
  "../trialBrowser.constants"
);

const shot = (overrides = {}) => ({
  when: "load",
  url: "https://shop.example.com/cart",
  link: true,
  tab: 1,
  at: 12.5,
  kept: true,
  picture_url: "http://storage/shot-1",
  ...overrides,
});

const listed = (overrides = {}) => ({
  state: "listed",
  capped: false,
  level_keeps_none: false,
  screenshots: [],
  more: 0,
  blocked_source: "record",
  ...overrides,
});

// Built at run time so the address is never a script literal in source.
const SCRIPT_ADDRESS = ["javascript", "alert(1)"].join(":");

const loaded = (data) => ({ data, isLoading: false, isError: false });

describe("TrialBrowserPanel", () => {
  beforeEach(() => {
    asked.length = 0;
    result = loaded(listed());
  });

  it("shows each screenshot in the record's order with its page, and says a row whose picture was not kept", () => {
    result = loaded(
      listed({
        screenshots: [
          shot(),
          shot({
            when: "end",
            url: "https://shop.example.com/pay",
            kept: false,
            picture_url: null,
            tab: 2,
          }),
        ],
      }),
    );
    render(<TrialBrowserPanel callExecutionId="c1" />);

    const [picture] = screen.getAllByRole("img");
    expect(picture).toHaveAttribute("src", "http://storage/shot-1");
    expect(picture).toHaveAttribute(
      "alt",
      C.alt("https://shop.example.com/cart"),
    );
    expect(screen.getAllByRole("img")).toHaveLength(1);
    expect(screen.getByText(C.notKept)).toBeInTheDocument();
    expect(
      screen
        .getAllByTestId("browser-screenshot")
        .map((one) => one.getAttribute("data-when")),
    ).toEqual(["load", "end"]);
    expect(asked[0][0]).toBe("c1");
  });

  it("links a page only when it is http or https, and shows any other address as text", () => {
    result = loaded(
      listed({
        screenshots: [
          shot({ url: SCRIPT_ADDRESS, link: true }),
          shot({ url: "https://shop.example.com/ok", link: true }),
          shot({ url: "https://shop.example.com/server-said-no", link: false }),
        ],
      }),
    );
    render(<TrialBrowserPanel callExecutionId="c1" />);

    expect(
      screen.getAllByRole("link").map((one) => one.getAttribute("href")),
    ).toEqual(["https://shop.example.com/ok"]);
    expect(screen.getByText(SCRIPT_ADDRESS)).toBeInTheDocument();
    expect(linkable({ url: "HTTP://x.test/", link: true })).toBe(true);
    expect(linkable({ url: "file:///etc/passwd", link: true })).toBe(false);
  });

  it.each([
    ["not_kept", C.states.not_kept],
    ["unreadable", C.states.unreadable],
    ["no_browser", C.states.no_browser],
  ])("says the %s state, never an empty list", (state, sentence) => {
    result = loaded(listed({ state }));
    render(<TrialBrowserPanel callExecutionId="c1" />);

    expect(screen.getByText(sentence)).toBeInTheDocument();
    expect(screen.queryByText(C.none)).toBeNull();
  });

  it("says when no screenshot was taken, when the level keeps none, when the cap was reached and how many more there are", () => {
    result = loaded(listed());
    const { unmount } = render(<TrialBrowserPanel callExecutionId="c1" />);
    expect(screen.getByText(C.none)).toBeInTheDocument();
    unmount();

    result = loaded(
      listed({
        level_keeps_none: true,
        capped: true,
        more: 3,
        complete: false,
        screenshots: [shot({ kept: false, picture_url: null })],
      }),
    );
    render(<TrialBrowserPanel callExecutionId="c1" />);
    expect(screen.getByText(C.levelKeepsNone)).toBeInTheDocument();
    // The level says it for every row; no row repeats it.
    expect(screen.queryByText(C.notKept)).toBeNull();
    expect(screen.getByText(C.capped)).toBeInTheDocument();
    expect(screen.getByText(C.more(3))).toBeInTheDocument();
    expect(screen.getByText(C.incomplete)).toBeInTheDocument();
  });

  it("shows a no-door trial's blocked list from the file, and says when it could not be read", () => {
    result = loaded(
      listed({
        blocked_source: "file",
        blocked: [
          {
            host: "www.example.com",
            via: "browser",
            what: "navigation",
            count: 1,
          },
          {
            host: "www.example.com",
            via: "browser",
            what: "navigation",
            count: 1,
          },
          {
            host: "cdn.other.test",
            via: "browser",
            what: "page_request",
            count: 6,
          },
        ],
        blocked_truncated: true,
      }),
    );
    const { unmount } = render(<TrialBrowserPanel callExecutionId="c1" />);
    expect(
      screen.getAllByTestId("blocked-page").map((one) => one.textContent),
    ).toEqual(["www.example.com ×2"]);
    expect(
      screen
        .getAllByTestId("blocked-page-request")
        .map((one) => one.textContent),
    ).toEqual(["cdn.other.test ×6"]);
    expect(screen.getByText(C.blockedTruncated)).toBeInTheDocument();
    unmount();

    result = loaded(listed({ blocked_source: "unreadable" }));
    const unread = render(<TrialBrowserPanel callExecutionId="c1" />);
    expect(screen.getByText(C.blockedUnreadable)).toBeInTheDocument();
    unread.unmount();

    // A door trial's lists are shown with its changes, never here.
    result = loaded(listed({ blocked_source: "record" }));
    render(<TrialBrowserPanel callExecutionId="c1" />);
    expect(screen.queryByTestId("blocked-page")).toBeNull();
    expect(screen.queryByText(C.blockedUnreadable)).toBeNull();
  });

  it("says when the browser could not be loaded, and says an earlier attempt's is not kept without asking", () => {
    result = { data: undefined, isLoading: false, isError: true };
    const { unmount } = render(<TrialBrowserPanel callExecutionId="c1" />);
    expect(screen.getByRole("alert")).toHaveTextContent(C.loadFailed);
    unmount();

    asked.length = 0;
    render(<TrialBrowserPanel callExecutionId="c1" attempt="a1" />);
    expect(screen.getByText(C.latestOnly)).toBeInTheDocument();
    expect(asked.every(([, options]) => options?.enabled === false)).toBe(true);
  });
});
