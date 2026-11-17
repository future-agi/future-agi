// Owner, Oct 20: "correct logo icons for the coding agent / frameworks".
// Every catalog entry shows its brand's own mark from the bundle (no
// Iconify API call, no monogram), and the run surfaces use the same mark.
import React from "react";
import { createHash } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { RUN_SOURCE_CARDS } from "../catalog";
import { ENDPOINTS, FRAMEWORKS, HARNESSES, RUNNING } from "../catalogEntries";

// dataset integration FE-0: the picker offers fewer sources, but every entry an existing
// run can name (frameworks, harnesses, endpoints, traces, uploads) keeps its
// bundled mark. The hosted voice platforms and the voice workers are gone.
const allEntries = () => [
  ...FRAMEWORKS,
  ...HARNESSES,
  ...ENDPOINTS,
  ...RUNNING,
];
import {
  BRAND_LOGOS,
  brandedEntryOfConfig,
  GENERIC_ICONS,
  brandLogoOf,
  sharesMark,
  withUniqueIds,
} from "../brandLogos";
import SourceLogo from "../SourceLogo";
import BundledIcon from "../BundledIcon";
import RunSourceBadge, {
  RUN_KIND_ICONS,
} from "src/sections/common/Runs/RunSourceBadge";

const iconify = vi.hoisted(() => ({ loadIcon: vi.fn(), rendered: vi.fn() }));
vi.mock("@iconify/react", () => {
  // Any Iconify render or load here would be a runtime fetch in the app.
  function MockIcon({ icon }) {
    iconify.rendered(icon);
    return <span data-iconify={String(icon)} />;
  }
  MockIcon.propTypes = { icon: () => null };
  return {
    Icon: MockIcon,
    loadIcon: iconify.loadIcon,
    iconExists: () => false,
  };
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const sha256 = (text) =>
  createHash("sha256").update(text, "utf8").digest("hex");
const bundledText = (m) => (m.mode === "png" ? m.src : m.svg);

const viewBoxOf = (svg) =>
  (svg.match(/viewBox="([^"]+)"/)?.[1] ?? "").split(/[\s,]+/).map(Number);

describe("bundled brand marks", () => {
  it("every catalog entry names a bundled mark with its source and licence", () => {
    const entries = allEntries();
    expect(entries).toHaveLength(33);
    entries.forEach((e) => {
      expect(e.logo.mark, e.id).toBe(e.id);
      const m = brandLogoOf(e.logo.mark);
      expect(m, e.id).toBeTruthy();
      expect(["mono", "colour", "png"]).toContain(m.mode);
      expect(m.source, e.id).toMatch(/^https:\/\//);
      expect(m.licence, e.id).toBeTruthy();
    });
    // One mark per entry, no orphans.
    expect(Object.keys(BRAND_LOGOS).sort()).toEqual(
      entries.map((e) => e.id).sort(),
    );
  });

  it("no catalog logo depends on a runtime fetch", () => {
    allEntries().forEach((e) => {
      expect(e.logo.iconify, e.id).toBeUndefined();
      expect(e.logo.asset, e.id).toBeUndefined();
      const m = brandLogoOf(e.logo.mark);
      if (m.mode === "png") {
        expect(m.src, e.id).toMatch(/^data:image\/png;base64,/);
        return;
      }
      expect(m.svg.startsWith("<svg"), e.id).toBe(true);
      expect(m.svg, e.id).not.toMatch(
        /<script|<image|<foreignObject|@import|href="(?!#)|url\((?!#)/i,
      );
      // Square viewBox, so every mark fills its box the same way.
      const [, , w, h] = viewBoxOf(m.svg);
      expect(w, e.id).toBeGreaterThan(0);
      expect(Math.abs(w - h), e.id).toBeLessThan(0.01);
      // No fixed size on the root: the box sets it (crisp at any px).
      expect(m.svg.match(/^<svg[^>]*>/)[0], e.id).not.toMatch(
        /\s(width|height)="/,
      );
    });
  });

  it("detects any change to a bundled file (sha256 change detector)", () => {
    // A swapped or edited file fails here until a new hash is recorded,
    // which is when its source row in brandLogos.js must be checked again.
    // (The hash is of the processed file, so it does not prove provenance;
    // SOURCES.md keeps the sha256 of each untouched upstream download.)
    allEntries().forEach((e) => {
      const m = brandLogoOf(e.logo.mark);
      expect(m.sha256, e.id).toMatch(/^[0-9a-f]{64}$/);
      expect(sha256(bundledText(m)), e.id).toBe(m.sha256);
    });
  });

  it("gives every brand its own mark (only the Claude spark and the OpenAI blossom are shared)", () => {
    const byHash = {};
    allEntries().forEach((e) => {
      const h = sha256(bundledText(brandLogoOf(e.logo.mark)));
      (byHash[h] ??= []).push(e.id);
    });
    const shared = Object.values(byHash)
      .filter((ids) => ids.length > 1)
      .map((ids) => [...ids].sort());
    expect(shared.sort()).toEqual([
      ["claude_agent_sdk", "claude_code"],
      ["codex_cli", "openai_agents"],
    ]);
    // The coding harness shows the OpenAI blossom, the
    // same file as the Agents SDK.
    expect(brandLogoOf("codex_cli").svg).toBe(brandLogoOf("openai_agents").svg);
  });

  it("renders all 33 marks with no Iconify load, no fetch and no monogram", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    render(
      <>
        {allEntries().map((e) => (
          <SourceLogo key={e.id} entry={e} size={20} />
        ))}
      </>,
    );
    allEntries().forEach((e) => {
      const el = document.querySelector(`[data-source-logo="${e.id}"]`);
      expect(el?.getAttribute("aria-label"), e.id).toBe(e.name);
      const kind = el?.querySelector("[data-logo-kind]")?.dataset.logoKind;
      expect(kind, e.id).toBe(e.id === "smolagents" ? "png" : "svg");
    });
    expect(
      document.querySelectorAll('[data-logo-kind="monogram"]'),
    ).toHaveLength(0);
    expect(iconify.loadIcon).not.toHaveBeenCalled();
    expect(iconify.rendered).not.toHaveBeenCalled();
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe("SourceLogo", () => {
  it("draws a one-colour mark in its colour and keeps a colour mark's own", () => {
    render(
      <>
        <SourceLogo entryId="crewai" size={24} />
        <SourceLogo entryId="openhands" size={24} />
        <SourceLogo entryId="google_adk" size={24} />
      </>,
    );
    const mark = (id) =>
      document.querySelector(`[data-source-logo="${id}"] [data-logo-kind]`);
    expect(mark("crewai").dataset.logoMode).toBe("mono");
    expect(getComputedStyle(mark("crewai")).color).toBe("rgb(255, 90, 80)");
    expect(mark("openhands").dataset.logoMode).toBe("mono");
    expect(mark("google_adk").dataset.logoMode).toBe("colour");
    // Google ADK is ADK's own robot mark (four Google colours), not the G.
    expect(mark("google_adk").innerHTML).toMatch(/#EA4335/i);
  });

  it("draws Codex CLI with the OpenAI blossom in one colour, like the Agents SDK", () => {
    // The same file as openai_agents,
    // drawn in currentColor so it follows light and dark mode.
    const codex = brandLogoOf("codex_cli");
    const sdk = brandLogoOf("openai_agents");
    expect(codex.mode).toBe("mono");
    expect(codex.svg).toBe(sdk.svg);
    expect(codex.sha256).toBe(sdk.sha256);
    expect(codex.source).toContain("openai-agents-python");
    // Not Custom command's console box.
    expect(codex.svg).not.toBe(brandLogoOf("custom_command").svg);
    render(<SourceLogo entryId="codex_cli" size={20} />);
    const el = document.querySelector(
      '[data-source-logo="codex_cli"] [data-logo-kind]',
    );
    expect(el.dataset.logoMode).toBe("mono");
  });

  it("keeps the official tile colours of LlamaIndex, Strands, AutoGen and Aider", () => {
    // Official colours, never a theme-inverted mono tile; a dark tile gets
    // a light edge in dark mode (darkEdge) instead.
    const expected = {
      llamaindex: ["#000", "#37D7FA", "#FF8705"],
      strands: ["#000", "#00cc60"],
      autogen: ["#2D2D2F", "#fff"],
      aider: ["#000", "#14B014"],
    };
    Object.entries(expected).forEach(([id, colours]) => {
      const m = brandLogoOf(id);
      expect(m.mode, id).toBe("colour");
      expect(m.darkEdge, id).toBe(true);
      expect(m.svg, id).not.toMatch(/currentColor/);
      colours.forEach((c) => expect(m.svg, `${id} ${c}`).toContain(c));
    });
  });

  it("shows smolagents' official mascot (inlined PNG) and Codex CLI the OpenAI blossom", () => {
    render(
      <>
        <SourceLogo entryId="smolagents" size={20} />
        <SourceLogo entryId="codex_cli" size={20} />
      </>,
    );
    const img = document.querySelector('[data-source-logo="smolagents"] img');
    expect(img.getAttribute("src")).toMatch(/^data:image\/png;base64,/);
    const codex = document.querySelector('[data-source-logo="codex_cli"] svg');
    expect(codex).toBeTruthy();
    // The OpenAI blossom the Agents SDK shows.
    expect(codex.querySelector("path").getAttribute("d")).toBe(
      brandLogoOf("openai_agents").svg.match(/ d="([^"]+)"/)[1],
    );
  });

  it("falls back to the monogram only when no mark ships", () => {
    render(
      <SourceLogo
        entry={{ id: "acme_bot", name: "Acme bot", logo: { monogram: "AB" } }}
        size={20}
      />,
    );
    expect(screen.getByText("AB")).toBeTruthy();
    expect(
      document.querySelector('[data-source-logo="acme_bot"] svg'),
    ).toBeNull();
  });

  it("names the brand for a saved worker that shows its own name", () => {
    render(
      <SourceLogo
        entry={{
          ...allEntries().find((e) => e.id === "claude_code"),
          name: "support-bot",
        }}
        size={20}
      />,
    );
    expect(
      document
        .querySelector('[data-source-logo="claude_code"]')
        .getAttribute("aria-label"),
    ).toBe("Claude Code");
  });

  it("gives each copy of a gradient mark its own ids", () => {
    render(
      <>
        <SourceLogo entryId="llamaindex" size={16} />
        <SourceLogo entryId="llamaindex" size={28} />
      </>,
    );
    const ids = [...document.querySelectorAll("[data-source-logo] [id]")].map(
      (n) => n.id,
    );
    expect(ids.length).toBeGreaterThanOrEqual(2);
    expect(new Set(ids).size).toBe(ids.length);
    document.querySelectorAll("[data-source-logo] svg").forEach((svg) => {
      const own = new Set([...svg.querySelectorAll("[id]")].map((n) => n.id));
      const refs = [...svg.innerHTML.matchAll(/url\(#([^)]+)\)/g)].map(
        (m) => m[1],
      );
      refs.forEach((r) => expect(own.has(r)).toBe(true));
    });
    expect(withUniqueIds('<g id="a" fill="url(#a)"/>', "x")).toBe(
      '<g id="a-x" fill="url(#a-x)"/>',
    );
  });
});

describe("run surfaces", () => {
  it("names the brand of a run config, never a generic kind", () => {
    const id = (config, kind) => brandedEntryOfConfig(config, kind)?.id ?? null;
    expect(id({ kind: "harness", harness: { id: "codex_cli" } })).toBe(
      "codex_cli",
    );
    expect(id({ framework: { id: "langgraph" } }, "code")).toBe("langgraph");
    expect(id({ kind: "voice", voice: { provider: "retell" } })).toBeNull();
    expect(id({ kind: "prompt" })).toBeNull();
    expect(id({ kind: "http" })).toBeNull();
    expect(id({ kind: "voice", voice: { provider: "phone" } })).toBeNull();
  });

  it("a run's source badge shows the source's own mark", () => {
    render(
      <>
        <RunSourceBadge
          run={{
            config: {
              kind: "harness",
              harness: { id: "claude_code", name: "Claude Code" },
            },
          }}
        />
        <RunSourceBadge
          run={{ config: { kind: "code", framework: { id: "crewai" } } }}
        />
        <RunSourceBadge run={{ config: { kind: "prompt" } }} />
      </>,
    );
    ["claude_code", "crewai"].forEach((id) =>
      expect(
        document.querySelector(`[data-source-logo="${id}"] svg`),
        id,
      ).toBeTruthy(),
    );
    // A brand's mark replaces the kind word; the word names it for readers.
    expect(screen.queryByText("Harness")).toBeNull();
    expect(
      document
        .querySelector('[data-run-kind="harness"]')
        .getAttribute("aria-label"),
    ).toBe("Harness · Claude Code");
    // A prompt run has no catalog entry: its kind icon, bundled, + word.
    expect(screen.getByText("Prompt")).toBeTruthy();
    expect(
      document.querySelector('[data-bundled-icon="mdi:text-box-outline"] svg'),
    ).toBeTruthy();
    expect(iconify.rendered).not.toHaveBeenCalled();
  });

  it("shows one icon per generic kind and brand marks at 16 px", () => {
    render(
      <>
        <RunSourceBadge run={{ name: "a", config: { kind: "http" } }} />
        <RunSourceBadge run={{ name: "b", kind: "http" }} />
        <RunSourceBadge
          run={{
            config: { kind: "harness", harness: { id: "custom_command" } },
          }}
        />
        <RunSourceBadge
          run={{ config: { kind: "harness", harness: { id: "aider" } } }}
        />
      </>,
    );
    // An endpoint run shows the endpoint kind icon whether or not its
    // config maps to the HTTP catalog entry; a custom command the console.
    const kindIcons = [...document.querySelectorAll("[data-run-kind]")].map(
      (n) =>
        n.querySelector("[data-bundled-icon]")?.dataset.bundledIcon ??
        n.querySelector("[data-source-logo]")?.dataset.sourceLogo,
    );
    // The endpoint icon is the catalog HTTP tile's mdi:api (the globe,
    // mdi:web, is the WebRTC / WebSocket tile).
    expect(kindIcons).toEqual(["mdi:api", "mdi:api", "mdi:console", "aider"]);
    expect(GENERIC_ICONS["mdi:api"]).toBe(
      brandLogoOf("http").svg.replace(
        'viewBox="3 3 18 18"',
        'viewBox="0 0 24 24"',
      ),
    );
    // The small badge never draws a brand below 16 px.
    const aider = document.querySelector(
      '[data-source-logo="aider"] [data-logo-kind]',
    );
    expect(aider.style.width || getComputedStyle(aider).width).toBe("16px");
    expect(iconify.rendered).not.toHaveBeenCalled();
  });

  it("every run-kind icon is bundled (no Iconify fallback)", () => {
    // BundledIcon falls back to the Iconify API for an id it lacks, so a
    // typo here would bring back a runtime fetch.
    Object.entries(RUN_KIND_ICONS).forEach(([kind, icon]) => {
      expect(GENERIC_ICONS[icon], `${kind} ${icon}`).toMatch(/^<svg/);
    });
    render(
      <>
        {Object.values(RUN_KIND_ICONS).map((icon) => (
          <BundledIcon key={icon} icon={icon} width={16} />
        ))}
      </>,
    );
    expect(iconify.rendered).not.toHaveBeenCalled();
  });

  it("keeps the name beside a mark two brands share (the Claude spark, the OpenAI blossom)", () => {
    expect(
      allEntries()
        .filter((e) => sharesMark(e.id))
        .map((e) => e.id)
        .sort(),
    ).toEqual([
      "claude_agent_sdk",
      "claude_code",
      "codex_cli",
      "openai_agents",
    ]);
    render(
      <>
        <RunSourceBadge
          run={{ config: { kind: "harness", harness: { id: "claude_code" } } }}
        />
        <RunSourceBadge
          run={{
            config: { kind: "code", framework: { id: "claude_agent_sdk" } },
          }}
        />
        <RunSourceBadge
          run={{ config: { kind: "harness", harness: { id: "codex_cli" } } }}
        />
        <RunSourceBadge
          run={{ config: { kind: "code", framework: { id: "crewai" } } }}
        />
      </>,
    );
    const [cc, sdk, codex, crew] = [
      ...document.querySelectorAll("[data-run-kind]"),
    ];
    expect(
      cc.querySelector('[data-source-logo="claude_code"] svg'),
    ).toBeTruthy();
    expect(cc.textContent.trim()).toBe("Claude Code");
    expect(
      sdk.querySelector('[data-source-logo="claude_agent_sdk"] svg'),
    ).toBeTruthy();
    expect(sdk.textContent.trim()).toBe("Claude Agent SDK");
    // Codex shares the OpenAI blossom, so its name stays beside it.
    expect(
      codex.querySelector('[data-source-logo="codex_cli"] svg'),
    ).toBeTruthy();
    expect(codex.textContent.trim()).toBe("Codex");
    // A mark only one brand uses stands alone.
    expect(crew.querySelector('[data-source-logo="crewai"] svg')).toBeTruthy();
    expect(crew.textContent.trim()).toBe("");
  });

  it("drops the shared-mark word when the run's own name already says it", () => {
    // A chip, the waiting banner and the Test a fix dock print the run's
    // name beside the badge, so `Claude Code 2.x` + `Claude Code` repeated.
    render(
      <>
        <RunSourceBadge
          run={{
            name: "Claude Code 2.x (headless -p)",
            config: { kind: "harness", harness: { id: "claude_code" } },
          }}
        />
        <RunSourceBadge
          run={{
            name: "claude agent sdk · support bot",
            config: { kind: "code", framework: { id: "claude_agent_sdk" } },
          }}
        />
        <RunSourceBadge
          run={{
            name: "Support Agent v3.2",
            config: { kind: "code", framework: { id: "claude_agent_sdk" } },
          }}
        />
      </>,
    );
    const [named, namedSdk, plain] = [
      ...document.querySelectorAll("[data-run-kind]"),
    ];
    expect(
      named.querySelector('[data-source-logo="claude_code"] svg'),
    ).toBeTruthy();
    expect(named.textContent.trim()).toBe("");
    expect(namedSdk.textContent.trim()).toBe("");
    // A name that does not say the brand keeps the word.
    expect(plain.textContent.trim()).toBe("Claude Agent SDK");
  });

  it("every step-1 card icon is bundled", () => {
    render(
      <>
        {RUN_SOURCE_CARDS.map((c) => (
          <BundledIcon key={c.id} icon={c.icon} width={18} />
        ))}
      </>,
    );
    RUN_SOURCE_CARDS.forEach((c) => {
      expect(GENERIC_ICONS[c.icon], c.icon).toMatch(/^<svg/);
      expect(
        document.querySelector(`[data-bundled-icon="${c.icon}"] svg`),
      ).toBeTruthy();
    });
    expect(iconify.rendered).not.toHaveBeenCalled();
  });
});
