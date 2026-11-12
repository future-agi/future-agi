import React from "react";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";

import rules from "../__fixtures__/rules.json";
import official from "../__fixtures__/entry-official.json";
import versions from "../__fixtures__/versions.json";
import meUser from "../__fixtures__/me-user.json";
import meMaintainer from "../__fixtures__/me-maintainer.json";
import HubEntryPage from "../HubEntryPage";
import CopyCommand from "../components/CopyCommand";
import { timeAgo } from "../lib/format";
import axios, { respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));
vi.mock("src/config-global", () => ({ HOST_API: "http://localhost:8000" }));

// The entry page as a visitor meets it, laid out like a package page: the
// Hub's own tab row, then a title block that holds the run command and the
// add command, one metadata line, a quiet Like and a More menu; the page
// reads What it does, then the Readme, Files and Versions tabs; the side
// column reads Use this, Keys, Disclosure, Provenance.

const ENTRY = "/omega-hub/v1/entries/future-agi/first-agent";
const AT = "/dashboard/hub/future-agi/first-agent";
const PINNED = "hub:future-agi/first-agent@1.0.0";
const REGISTRY = "--registry http://localhost:8000/omega-hub/v1";
const CLI = [OMEGA_PART.HUB_CLI];

function serve(
  entry = structuredClone(official),
  { parts = CLI, me = meUser, rows = versions, extra = [] } = {},
) {
  respond([
    ...extra,
    ["get", "/api/capabilities/", capabilitiesAnswer({ parts })],
    ["get", ENTRY, entry],
    ["get", `${ENTRY}/versions`, rows],
    ["get", `${ENTRY}/remixes`, []],
    ["get", "/omega-hub/v1/rules", rules],
    ["get", "/omega-hub/v1/me", me],
    [
      "get",
      /^\/omega-hub\/v1\/publishers\//,
      { ...entry.publisher, entryCount: 3 },
    ],
  ]);
}

const open = (at = AT) =>
  renderAt(<HubEntryPage />, {
    path: "/dashboard/hub/:publisher/:name/:tab?",
    at,
  });

// Let /api/capabilities/ answer, so a gate that would open has had its chance.
const settle = () => new Promise((resolve) => setTimeout(resolve, 50));

const codeLines = (box) =>
  [...box.querySelectorAll("code")].map((code) => code.textContent);

// How long ago the fixture's version was approved, in the Hub's one wording,
// worked out the same way the page does so the case reads true on any day.
const APPROVED_AGO = timeAgo(official.latest.approvedAt);

// Defined, not assigned: user-event's setup() leaves a getter-only stub.
function clipboard() {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
    writable: true,
  });
  return writeText;
}

beforeEach(() => vi.stubEnv("VITE_OMEGA_CONSOLE", "1"));
afterEach(() => vi.unstubAllEnvs());

describe("The Hub's own tab row", () => {
  it("sits above the title block with Browse selected, so a shared entry link still reaches the rest of the Hub", async () => {
    serve();
    open();
    const nav = await screen.findByTestId("hub-nav-main");
    const header = await screen.findByTestId("hub-entry-header");
    expect(
      nav.compareDocumentPosition(header) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(within(nav).getByRole("tab", { name: "Browse" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    for (const name of ["Publishers", "My submissions", "Settings"]) {
      expect(within(nav).getByRole("tab", { name })).toHaveAttribute(
        "aria-selected",
        "false",
      );
    }
  });
});

describe("The title block holds the main action", () => {
  it("Try it now with the whole run command, then Add it to your project, then the download", async () => {
    serve();
    open();
    const header = await screen.findByTestId("hub-entry-header");
    const tryIt = await within(header).findByRole("region", {
      name: "Try it now",
    });
    expect(codeLines(within(tryIt).getByTestId("hub-cli-run"))).toEqual([
      `omega hub run ${PINNED} ${REGISTRY}`,
    ]);
    expect(tryIt).toHaveTextContent(
      "Runs without a key. A scripted model stands in for a live one.",
    );
    const add = within(tryIt).getByRole("heading", {
      name: "Add it to your project",
    });
    const addBox = within(tryIt).getByTestId("hub-cli-add");
    expect(add.nextElementSibling).toBe(addBox);
    expect(codeLines(addBox)).toEqual([`omega hub add ${PINNED} ${REGISTRY}`]);
    expect(
      within(tryIt).getByRole("link", { name: "Download (.tar.gz)" }),
    ).toHaveAttribute(
      "href",
      `http://localhost:8000${ENTRY}/versions/1.0.0/bundle?source=web`,
    );
  });

  it("without a released command line, the same place offers the download and names no command", async () => {
    serve(structuredClone(official), { parts: [] });
    open();
    const header = await screen.findByTestId("hub-entry-header");
    const files = await within(header).findByRole("region", {
      name: "Get the files",
    });
    await settle();
    expect(
      within(files).getByRole("link", { name: "Download (.tar.gz)" }),
    ).toHaveAttribute("target", "_blank");
    expect(files).toHaveTextContent(
      "The approved files of version 1.0.0 in one archive, 4.3 KB.",
    );
    expect(screen.queryByTestId("hub-cli-run")).toBeNull();
    expect(header.textContent).not.toMatch(/omega hub/);
  });
});

describe("The entry's reference sits under its title", () => {
  it("shows publisher/name with a button that copies the pinned reference", async () => {
    const writeText = clipboard();
    serve(structuredClone(official), { parts: [] });
    open();
    const header = await screen.findByTestId("hub-entry-header");
    expect(within(header).getByTestId("hub-entry-ref")).toHaveTextContent(
      "future-agi/first-agent",
    );
    fireEvent.click(
      within(header).getByRole("button", {
        name: "Copy reference future-agi/first-agent",
      }),
    );
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(PINNED));
  });
});

describe("A command is shown whole and wraps at its own boundaries", () => {
  it("breaks only at spaces and after each / and : (never inside ://), and copies the exact text", async () => {
    const text = `omega hub add ${PINNED} --registry https://fa.example.test/__api/omega-hub/v1`;
    const writeText = clipboard();
    respond([]);
    renderAt(<CopyCommand text={text} testId="cmd" />);
    const code = screen.getByTestId("cmd").querySelector("code");
    expect(code.textContent).toBe(text);
    expect(code).toHaveAttribute("title", text);
    expect(getComputedStyle(code).whiteSpace).toBe("pre-wrap");
    const pieces = [""];
    for (const node of code.childNodes) {
      if (node.nodeName === "WBR") pieces.push("");
      else pieces[pieces.length - 1] += node.textContent;
    }
    expect(pieces).toEqual([
      "omega hub add hub:",
      "future-agi/",
      "first-agent@1.0.0 --registry https://",
      "fa.example.test/",
      "__api/",
      "omega-hub/",
      "v1",
    ]);
    expect([...code.children].every((node) => node.tagName === "WBR")).toBe(
      true,
    );
    fireEvent.click(screen.getByRole("button", { name: `Copy ${text}` }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(text));
  });
});

describe("One metadata line, a quiet Like and a More menu", () => {
  it("reads kind, level, licence and version, with no version picker for one version", async () => {
    serve();
    open();
    const facts = await screen.findByTestId("hub-entry-facts");
    expect(facts.textContent).toBe(
      "Agent · Starter · Apache-2.0 · Version 1.0.0",
    );
    expect(screen.queryByRole("combobox", { name: "Version" })).toBeNull();
  });

  it("the byline says when the version was approved, with how long ago in plain view", async () => {
    serve();
    open();
    const byline = await screen.findByTestId("hub-entry-byline");
    expect(byline).toHaveTextContent(
      `Checked and approved 14 Oct 2026 (${APPROVED_AGO})`,
    );
    expect(byline.querySelector("time")).toHaveAttribute(
      "datetime",
      new Date(official.latest.approvedAt).toISOString(),
    );
  });

  it("More holds Copy reference, Download .tar.gz and Report a problem; a visitor sees no Maintainer group", async () => {
    const user = userEvent.setup();
    const writeText = clipboard();
    serve();
    open();
    const header = await screen.findByTestId("hub-entry-header");
    expect(within(header).getByTestId("hub-like-button")).toHaveClass(
      "MuiButton-text",
    );
    const more = within(header).getByRole("button", { name: "More" });
    await settle();
    await user.click(more);
    let menu = await screen.findByRole("menu");
    expect(
      within(menu)
        .getAllByRole("menuitem")
        .map((item) => item.textContent),
    ).toEqual(["Copy reference", "Download .tar.gz", "Report a problem"]);
    expect(within(menu).queryByText("Maintainer")).toBeNull();
    expect(
      within(menu).getByRole("menuitem", { name: "Download .tar.gz" }),
    ).toHaveAttribute(
      "href",
      `http://localhost:8000${ENTRY}/versions/1.0.0/bundle?source=web`,
    );
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("menu")).toBeNull());
    await waitFor(() => expect(more).toHaveFocus());

    await user.click(more);
    menu = await screen.findByRole("menu");
    await user.click(
      within(menu).getByRole("menuitem", { name: "Copy reference" }),
    );
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(PINNED));

    await user.click(more);
    menu = await screen.findByRole("menu");
    await user.click(
      within(menu).getByRole("menuitem", { name: "Report a problem" }),
    );
    expect(await screen.findByTestId("hub-report-dialog")).toBeInTheDocument();
  });

  it("a maintainer finds every moderation action under Maintainer, and a removal confirms with the entry's name", async () => {
    serve(structuredClone(official), { me: meMaintainer });
    open();
    const more = await screen.findByRole("button", { name: "More" });
    fireEvent.click(more);
    const menu = await screen.findByRole("menu");
    await waitFor(() =>
      expect(within(menu).getByText("Maintainer")).toBeInTheDocument(),
    );
    expect(
      within(menu)
        .getAllByRole("menuitem")
        .map((item) => item.textContent),
    ).toEqual([
      "Copy reference",
      "Download .tar.gz",
      "Report a problem",
      "Unrank",
      "Yank version 1.0.0",
      "Remove",
      "Move or rename",
      "Show likers",
    ]);
    expect(screen.queryByTestId("hub-moderate-button")).toBeNull();
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Remove" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading")).toHaveTextContent(
      "Remove future-agi/first-agent?",
    );
  });
});

describe("The page reads in order", () => {
  it("What it does, then Readme, Files and Versions; the Readme leaves out what is shown above and pins its commands", async () => {
    serve();
    open();
    const what = await screen.findByRole("heading", {
      level: 2,
      name: "What it does",
    });
    const tablist = screen.getByRole("tablist", {
      name: "More about this entry",
    });
    expect(
      what.compareDocumentPosition(tablist) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(screen.getByTestId("hub-entry-what")).toHaveTextContent(
      "The smallest useful Omega agent.",
    );
    expect(
      within(tablist)
        .getAllByRole("tab")
        .map((tab) => tab.textContent),
    ).toEqual(["Readme", "Files 8", "Versions 1"]);
    const readme = screen.getByTestId("hub-readme");
    expect(
      within(readme).queryByRole("heading", { name: "What it does" }),
    ).toBeNull();
    expect(
      within(readme).queryByRole("heading", { name: official.title }),
    ).toBeNull();
    expect(
      within(readme).getByRole("heading", { name: "Run it with your key" }),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(readme).toHaveTextContent(
        `omega hub run ${PINNED} ${REGISTRY} --live openai:gpt-4o-mini`,
      ),
    );
    expect(readme).toHaveTextContent("omega hub check first-agent --record");
    expect(readme.textContent).not.toMatch(/omega hub run first-agent\b/);
  });
});

describe("A version with no README", () => {
  it("says so and offers its files as the next thing to read", async () => {
    const entry = structuredClone(official);
    entry.latest.readme = "";
    serve(entry);
    open();
    const empty = await screen.findByTestId("hub-entry-no-readme");
    expect(empty).toHaveTextContent("This version has no README.");
    fireEvent.click(
      within(empty).getByRole("button", { name: "Open the files" }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(`${AT}/files`),
    );
    expect(await screen.findByTestId("hub-entry-files")).toBeInTheDocument();
  });
});

describe("The side column: Use this, Keys, Disclosure, Provenance", () => {
  it("Keys: none needed to try it; for a live run any one of these, each with what it is, where to get it and a copy button", async () => {
    const writeText = clipboard();
    serve();
    open();
    const keys = await screen.findByRole("region", { name: "Keys" });
    expect(keys).toHaveTextContent("None needed to try it.");
    expect(keys).toHaveTextContent("For a live run, any one of these.");
    for (const key of official.latest.manifest.keys) {
      const item = within(keys).getByText(key.name).closest("li");
      expect(item).toHaveTextContent(key.why);
      expect(
        within(item).getByRole("link", {
          name: `Get it at ${new URL(key.get).host}`,
        }),
      ).toHaveAttribute("href", key.get);
      expect(
        within(item).getByRole("button", { name: `Copy ${key.name}` }),
      ).toBeInTheDocument();
    }
    fireEvent.click(
      within(keys).getByRole("button", { name: "Copy OPENAI_API_KEY" }),
    );
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith("OPENAI_API_KEY"),
    );
  });

  it("Use this swaps the key and the --live model together, and states the call cap", async () => {
    serve();
    open();
    const use = await screen.findByRole("region", { name: "Use this" });
    const live = () => codeLines(within(use).getByTestId("hub-cli-live"));
    expect(live()).toEqual([
      "export OPENAI_API_KEY=...",
      `omega hub run ${PINNED} --live openai:gpt-4o-mini ${REGISTRY}`,
    ]);
    expect(use).toHaveTextContent(
      "A live run makes at most 2 model calls, billed by the model vendor to your key.",
    );
    fireEvent.mouseDown(
      within(use).getByRole("combobox", { name: /On your own model/ }),
    );
    fireEvent.click(
      await screen.findByRole("option", {
        name: "anthropic:claude-sonnet-4-6",
      }),
    );
    await waitFor(() =>
      expect(live()).toEqual([
        "export ANTHROPIC_API_KEY=...",
        `omega hub run ${PINNED} --live anthropic:claude-sonnet-4-6 ${REGISTRY}`,
      ]),
    );
  });

  it("draws no Use this for an entry with no live model, and Keys says it needs none", async () => {
    const entry = structuredClone(official);
    delete entry.latest.manifest.models;
    entry.latest.manifest.keys = [];
    serve(entry);
    open();
    await screen.findByRole("region", { name: "Try it now" });
    await settle();
    expect(screen.queryByTestId("hub-use-this")).toBeNull();
    expect(screen.getByRole("region", { name: "Keys" })).toHaveTextContent(
      "None. It runs without a key.",
    );
  });

  it("reads Use this, Keys, Disclosure, Provenance, with no installs card at zero and no commit it does not have", async () => {
    serve();
    open();
    await screen.findByTestId("hub-use-this");
    const rail = screen.getByTestId("hub-entry-rail");
    const order = [...rail.children]
      .map((node) => node.getAttribute("data-testid"))
      .filter(Boolean);
    expect(order.slice(0, 4)).toEqual([
      "hub-use-this",
      "hub-keys",
      "hub-disclosure",
      "hub-provenance",
    ]);
    const provenance = within(rail).getByRole("region", {
      name: "Provenance",
    });
    expect(provenance).toHaveTextContent(
      `Checked and approved on 14 Oct 2026 (${APPROVED_AGO}) by a Future AGI maintainer`,
    );
    expect(provenance).toHaveTextContent("sha256:7465d5950300");
    expect(provenance.textContent).not.toMatch(/commit/i);
    expect(screen.getByTestId("hub-entry-byline").textContent).not.toMatch(
      /commit/i,
    );
    expect(screen.queryByTestId("hub-installs")).toBeNull();
  });

  it("lists each check's result as the page loads, and folds the list on request", async () => {
    serve();
    open();
    const provenance = await screen.findByRole("region", {
      name: "Provenance",
    });
    const steps = Object.keys(official.latest.checkSummary.steps);
    const checks = within(provenance).getByRole("list", { name: "Checks" });
    expect(within(checks).getAllByRole("listitem")).toHaveLength(steps.length);
    expect(
      within(checks).getByText("Sandbox run").closest("li"),
    ).toHaveTextContent("passed");
    expect(
      within(checks).getByText("Static scan").closest("li"),
    ).toHaveTextContent("flagged, reviewed");
    const hide = within(provenance).getByRole("button", {
      name: "Hide the checks",
    });
    expect(hide).toHaveAttribute("aria-expanded", "true");
    expect(hide).toHaveAttribute("aria-controls", checks.id);
    fireEvent.click(hide);
    expect(
      within(provenance).getByRole("button", {
        name: `Show the ${steps.length} checks`,
      }),
    ).toHaveAttribute("aria-expanded", "false");
    await waitFor(() =>
      expect(
        within(provenance).queryByRole("list", { name: "Checks" }),
      ).toBeNull(),
    );
  });

  it("a disclosure part left empty says it is not stated, never a bare dash", async () => {
    const entry = structuredClone(official);
    entry.latest.disclosure = { ...entry.latest.disclosure, background: "" };
    serve(entry);
    open();
    const disclosure = await screen.findByRole("region", {
      name: "Disclosure",
    });
    const label = within(disclosure).getByText("Keeps running");
    expect(label.nextElementSibling).toHaveTextContent(/^Not stated\.$/);
    expect(disclosure.textContent).not.toContain("—");
  });

  it("names the source commit in the byline and in Provenance when the version has one", async () => {
    const entry = structuredClone(official);
    entry.latest.sourceCommit = "0123abcd9876";
    serve(entry);
    open();
    const provenance = await screen.findByRole("region", {
      name: "Provenance",
    });
    expect(provenance).toHaveTextContent("From commit 0123abc");
    expect(screen.getByTestId("hub-entry-byline")).toHaveTextContent(
      "from commit 0123abc",
    );
  });
});

describe("Remixes", () => {
  it("shows no zero count, and the card has its own heading like the rest of the side column", async () => {
    serve();
    open();
    const remixes = await screen.findByRole("region", { name: "Remixes" });
    expect(
      within(remixes).getByRole("heading", { level: 2, name: "Remixes" }),
    ).toBeInTheDocument();
    expect(within(remixes).queryByTestId("hub-remix-count")).toBeNull();
    expect(remixes.textContent).not.toMatch(/\(0\)/);
  });

  it("counts the remixes once there are any, in the heading, without saying Remixes twice", async () => {
    serve({ ...structuredClone(official), remixCount: 3 });
    open();
    const remixes = await screen.findByRole("region", { name: "Remixes 3" });
    const heading = within(remixes).getByRole("heading", { level: 2 });
    expect(heading).toHaveTextContent(/^Remixes 3$/);
    expect(within(heading).getByTestId("hub-remix-count").textContent).toBe(
      "3",
    );
    expect(remixes.textContent.match(/Remixes/g)).toHaveLength(1);
  });
});

describe("Versions and Files", () => {
  const rows = [
    {
      ...versions[0],
      version: "1.0.0",
      publishedAt: "2026-10-14T13:40:35+00:00",
      notes: "First listed version.",
    },
    {
      ...versions[0],
      version: "1.2.0",
      publishedAt: "2026-11-02T09:00:00+00:00",
      status: "yanked",
      yankedReason: "Prints a key in its log.",
    },
    {
      ...versions[0],
      version: "1.10.0",
      publishedAt: "2026-10-20T09:00:00+00:00",
    },
  ];
  const order = () =>
    screen
      .getAllByTestId("hub-version-row")
      .map((row) => row.getAttribute("data-version"));

  it("the Versions tab is a table of version, published date, status and notes, sortable, where each version opens", async () => {
    serve(structuredClone(official), { rows });
    open(`${AT}/versions`);
    const table = await screen.findByRole("table", { name: "Versions" });
    expect(
      within(table)
        .getAllByRole("columnheader")
        .map((cell) => cell.textContent),
    ).toEqual(["Version", "Published", "Status", "Notes"]);
    expect(order()).toEqual(["1.2.0", "1.10.0", "1.0.0"]);
    const yanked = screen
      .getAllByTestId("hub-version-row")
      .find((row) => row.getAttribute("data-version") === "1.2.0");
    expect(yanked).toHaveTextContent("Yanked");
    expect(yanked).toHaveTextContent("Prints a key in its log.");
    expect(yanked).toHaveTextContent("2 Nov 2026");
    fireEvent.click(within(table).getByRole("button", { name: "Version" }));
    expect(order()).toEqual(["1.10.0", "1.2.0", "1.0.0"]);
    fireEvent.click(within(table).getByRole("button", { name: "Version" }));
    expect(order()).toEqual(["1.0.0", "1.2.0", "1.10.0"]);
    expect(within(table).getByRole("link", { name: "1.0.0" })).toHaveAttribute(
      "href",
      AT,
    );
    fireEvent.click(within(table).getByRole("link", { name: "1.2.0" }));
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        `${AT}?version=1.2.0`,
      ),
    );
  });

  it("each version names its bundle digest and rules version under its notes", async () => {
    serve(structuredClone(official), { rows });
    open(`${AT}/versions`);
    await screen.findByRole("table", { name: "Versions" });
    for (const line of screen.getAllByTestId("hub-version-row")) {
      const bundle = within(line).getByTestId("hub-version-bundle");
      expect(bundle).toHaveTextContent("sha256:7465d5950300");
      expect(bundle).toHaveTextContent("rules v1");
      expect(within(bundle).getByTitle(versions[0].digest)).toBeInTheDocument();
      // In the Notes cell, not a column of its own.
      expect(bundle.closest("td")).toBe(within(line).getAllByRole("cell")[3]);
    }
    const first = screen
      .getAllByTestId("hub-version-row")
      .find((row) => row.getAttribute("data-version") === "1.0.0");
    const notes = within(first).getByText("First listed version.");
    expect(
      notes.compareDocumentPosition(
        within(first).getByTestId("hub-version-bundle"),
      ) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("a published date that does not parse reads as a dash, with no 'NaN days ago' under it", async () => {
    serve(structuredClone(official), {
      rows: [{ ...versions[0], publishedAt: "not a date" }],
    });
    open(`${AT}/versions`);
    const table = await screen.findByRole("table", { name: "Versions" });
    const row = within(table).getByTestId("hub-version-row");
    const published = within(row).getAllByRole("cell")[1];
    expect(published.textContent).toBe("—");
    expect(row.textContent).not.toMatch(/NaN/);
  });

  it("each published date shows how long ago it was in plain view, not only on hover", async () => {
    serve(structuredClone(official), { rows });
    open(`${AT}/versions`);
    await screen.findByRole("table", { name: "Versions" });
    for (const row of rows) {
      const line = screen
        .getAllByTestId("hub-version-row")
        .find((node) => node.getAttribute("data-version") === row.version);
      expect(within(line).getByText(timeAgo(row.publishedAt))).toBeVisible();
    }
  });

  it("the Files tab lists sizes, and the viewer names the open file and its size", async () => {
    serve(structuredClone(official), {
      extra: [
        [
          "get",
          `${ENTRY}/versions/1.0.0/files/README.md`,
          official.latest.readme,
        ],
      ],
    });
    open(`${AT}/files`);
    const name = await screen.findByRole("heading", {
      level: 3,
      name: "README.md",
    });
    expect(name.parentElement).toHaveTextContent("1.4 KB");
    expect(await screen.findByTestId("hub-file-viewer")).toHaveTextContent(
      "Your first agent in one file",
    );
    expect(screen.getByTestId("hub-entry-files")).toHaveTextContent("8 files");
  });
});

describe("Loading and failure", () => {
  it("shows skeletons shaped like the header, the side column and the tabs while the entry loads", async () => {
    serve();
    const original = axios.get.getMockImplementation();
    const pending = vi
      .spyOn(axios, "get")
      .mockImplementation((url, config) =>
        String(url).split("?")[0] === ENTRY
          ? new Promise(() => {})
          : original(url, config),
      );
    try {
      open();
      expect(
        await screen.findByTestId("hub-loading-header"),
      ).toBeInTheDocument();
      expect(screen.getByTestId("hub-loading-side")).toBeInTheDocument();
      expect(screen.getByTestId("hub-loading-tabs")).toBeInTheDocument();
    } finally {
      pending.mockImplementation(original);
    }
  });

  it("says which entry did not load and offers Try again", async () => {
    serve(structuredClone(official), {
      extra: [
        [
          "get",
          ENTRY,
          { error: { message: "The registry refused the lookup." } },
          400,
        ],
      ],
    });
    open();
    const error = await screen.findByTestId("hub-error");
    expect(error).toHaveTextContent("future-agi/first-agent did not load");
    expect(error).toHaveTextContent("The registry refused the lookup.");
    expect(
      within(error).getByRole("button", { name: "Try again" }),
    ).toBeInTheDocument();
  });
});
