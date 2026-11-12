import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { afterEach, describe, expect, it } from "vitest";
import fixture from "../__fixtures__/inventory-classes.json";
import {
  ADD_ROWS_TILE_SURFACE,
  LAUNCH_DATASET_PAGE_KEYS,
  LAUNCH_SURFACES,
  ON_DATASET_PAGE,
  ROW_EXTRA_TABS,
  SURFACE_BY_ID,
  matchesGlob,
  surfacesOfOption,
} from "../launchSurfaces";
import {
  EXPERIMENTS_BASIC_DEFAULT,
  addRowsTilesIn,
  datasetTabShownIn,
  experimentsBasicFromEnv,
  glossaryShownIn,
  hiddenSurfaceIds,
  launchBuildFromEnv,
  setLaunchOverrideForTest,
  shortcutShownIn,
  surfaceShownIn,
  verbShownIn,
} from "../launchSwitch";
import { MADE_REAL, MADE_REAL_PATHS } from "../madeReal";
import { launchRedirect } from "../launchRoutes";
import { sanitizeLaunchSearch } from "../launchSearch";
import { scanDatasetPageKeys } from "./datasetPageKeys";
import {
  ADD_ROWS_OPTIONS,
  ROW_IMPORT_OPTIONS,
} from "src/sections/develop/importOptions";

// L6 launch switch (the capability contract): the registry covers every option the
// inventory does not class REAL-TODAY, the default launch build hides them,
// and no entry path (route, deep-link key, palette verb, shortcut, tab)
// reaches a hidden surface.

const LAUNCH = { launch: true, experimentsBasic: false, madeReal: new Set() };
const BASIC = { launch: true, experimentsBasic: true, madeReal: new Set() };
const PROTO = { launch: false, experimentsBasic: false, madeReal: new Set() };

afterEach(() => setLaunchOverrideForTest(null));

const SAMPLE_PAGE = {
  ON_DATASET_PAGE: "/dashboard/develop/9f0c2d1e-1111-4a2b-9c3d-000000000001",
  ON_EVALS: "/dashboard/evaluations/t1",
  ON_ANY: "/dashboard/develop",
};
const pageFor = (re) =>
  Object.values(SAMPLE_PAGE).find((p) => re.test(p)) ?? null;
const sampleOf = (glob) => glob.replace(/\*/g, "x");

describe("L6 launch switch: build modes", () => {
  it("is a launch build outside the prototype and unit tests", () => {
    expect(
      launchBuildFromEnv({ MODE: "production", VITE_ENVIRONMENT: "dev" }),
    ).toBe(true);
    expect(
      launchBuildFromEnv({
        MODE: "production",
        VITE_ENVIRONMENT: "production",
      }),
    ).toBe(true);
    expect(
      launchBuildFromEnv({ MODE: "development", VITE_ENVIRONMENT: "local" }),
    ).toBe(true);
    expect(
      launchBuildFromEnv({
        MODE: "production",
        VITE_ENVIRONMENT: "local",
        VITE_PROTOTYPE_MODE: "true",
      }),
    ).toBe(false);
    expect(launchBuildFromEnv({ MODE: "test" })).toBe(false);
  });

  it("defaults experimentsBasic ON (all-on flip); an explicit false still turns it off", () => {
    expect(EXPERIMENTS_BASIC_DEFAULT).toBe(true);
    expect(experimentsBasicFromEnv({})).toBe(true);
    expect(
      experimentsBasicFromEnv({ VITE_DSX_EXPERIMENTS_BASIC: "true" }),
    ).toBe(true);
    expect(
      experimentsBasicFromEnv({ VITE_DSX_EXPERIMENTS_BASIC: "false" }),
    ).toBe(false);
  });

  it("shows everything in a prototype (or unit-test) build", () => {
    LAUNCH_SURFACES.forEach((s) =>
      expect(surfaceShownIn(s.id, PROTO), s.id).toBe(true),
    );
    expect(
      launchRedirect(
        { pathname: "/dashboard/develop/experiment/e1/matrix" },
        PROTO,
      ),
    ).toBeNull();
    expect(
      sanitizeLaunchSearch(
        "?new_experiment=1&add=production",
        SAMPLE_PAGE.ON_DATASET_PAGE,
        PROTO,
      ).changed,
    ).toBe(false);
  });

  it("throws on an unknown surface id (a typo never shows a surface)", () => {
    expect(() => surfaceShownIn("exp.nope", LAUNCH)).toThrow(
      /Unknown launch surface/,
    );
  });
});

describe("L6 launch switch: the registry covers the inventory", () => {
  const classes = fixture.classes;

  it("snapshots the corrected inventory (708 options; 66 real today)", () => {
    const count = (c) => Object.values(classes).filter((x) => x === c).length;
    expect(Object.keys(classes)).toHaveLength(708);
    expect(count("REAL-TODAY")).toBe(66);
    expect(count("NEEDS-ADAPTER")).toBe(152);
    expect(count("NEEDS-ENDPOINT")).toBe(429);
    expect(count("MOCK-ONLY")).toBe(61);
  });

  it("puts every option that is not REAL-TODAY under a hidden surface (or L6's own fixes)", () => {
    const uncovered = Object.entries(classes)
      .filter(([, cls]) => cls !== "REAL-TODAY")
      .filter(([id]) => {
        const hosts = surfacesOfOption(id);
        const hidden = hosts.some((s) => !surfaceShownIn(s.id, LAUNCH));
        const l6Fix = hosts.some((s) => s.id === "launch.fixes");
        // DSX J09: precisely the restored legacy import doors.
        const nohideImport = [
          "create:hf",
          "rows:hf",
          "create:blank-grid",
        ].includes(id);
        // Census section A / NOHIDE: restored v1.56.1 classic options,
        // not NEW capability claims; keep the uncovered census exactly empty.
        const nohideClassic = [
          "entry:new-experiment-button",
          "card:prompt",
          "knob:prompt:version",
          "knob:prompt:models",
          "knob:prompt:model-params",
          "knob:prompt:variables",
          "exp:name",
          "exp:output-format",
          "wiz:add-evaluation",
          "wiz:card-edit",
          "wiz:card-remove",
          "wiz:edit-experiment-evals",
          "wiz:eval-step",
          "sum:all-runs",
        ].includes(id);
        return !hidden && !l6Fix && !nohideImport && !nohideClassic;
      })
      .map(([id]) => id);
    expect(uncovered).toEqual([]);
  });

  it("lists only fixture ids (no stale pattern) and only L6 fixes as shown non-real", () => {
    const ids = Object.keys(classes);
    LAUNCH_SURFACES.forEach((s) =>
      s.options.forEach((g) =>
        expect(
          ids.some((id) => matchesGlob(g, id)),
          `${s.id} ${g}`,
        ).toBe(true),
      ),
    );
    const shownNonReal = LAUNCH_SURFACES.filter(
      (s) => s.ships === "shown" && s.id !== "shell.real",
    ).flatMap((s) => s.options);
    expect(new Set(shownNonReal)).toEqual(
      new Set([
        ...SURFACE_BY_ID["launch.fixes"].options,
        // DSX J09; HF is visible but disabled.
        "create:hf",
        "rows:hf",
        "create:blank-grid",
        // Census G2 / NOHIDE: restored legacy Add rows options, not NEW paste.
        "rows:add-rows-sheet",
        "rows:upload-file",
        "rows:copy-from-dataset",
        // Census section A / NOHIDE: restored v1.56.1 classic experiment
        // options, not NEW capability claims. Keep this literal Set exact.
        "entry:new-experiment-button",
        "card:prompt",
        "knob:prompt:version",
        "knob:prompt:models",
        "knob:prompt:model-params",
        "knob:prompt:variables",
        "exp:name",
        "exp:output-format",
        "wiz:add-evaluation",
        "wiz:card-edit",
        "wiz:card-remove",
        "wiz:edit-experiment-evals",
        "wiz:eval-step",
        "sum:all-runs",
        // All-on flip (experimentsBasic family): shown behind the flag.
        ...SURFACE_BY_ID["exp.new"].options,
        ...SURFACE_BY_ID["dataset.tab.experiments"].options,
        ...SURFACE_BY_ID["dataset.experimentDefaults"].options,
      ]),
    );
  });

  it("hides the uninventoried W7 types, the reads table and the connect world by default", () => {
    const hidden = hiddenSurfaceIds(LAUNCH);
    [
      "evals.dag",
      "evals.remote",
      "evals.jury",
      "evals.agreement",
      "exp.new",
      "exp.new.readsTable",
      "exp.new.runningSources",
      "exp.new.scope",
      "exp.results.matrix",
      "exp.compare",
      "exp.page",
      "dataset.tab.experiments",
      "dataset.tab.summary",
      "dataset.fromProduction",
      "runners.connect",
      "palette.runs",
      "hf.revision",
      "hf.curated",
    ].forEach((id) => expect(hidden, id).toContain(id));
    // DSX J09 reverses only these legacy hidden states.
    ["hf.addToExisting", "hf.create", "dataset.imports.blankGrid"].forEach(
      (id) => expect(hidden, id).not.toContain(id),
    );
  });

  it("experimentsBasic shows New experiment, the experiment page and the Experiments tab only", () => {
    expect(surfaceShownIn("exp.new", BASIC)).toBe(true);
    expect(surfaceShownIn("exp.page", BASIC)).toBe(true);
    // All-on flip: the Experiments tab ships shown behind the flag.
    expect(surfaceShownIn("dataset.tab.experiments", BASIC)).toBe(true);
    [
      "exp.new.readsTable",
      "exp.new.runningSources",
      "exp.new.scope",
      "exp.new.kindMenu",
      "exp.results.matrix",
      "exp.compare",
      "palette.runs",
    ].forEach((id) => expect(surfaceShownIn(id, BASIC), id).toBe(false));
  });

  it("an experiments surface a lane makes real still needs experimentsBasic", () => {
    const l3 = { ...LAUNCH, madeReal: new Set(["dataset.tab.experiments"]) };
    expect(surfaceShownIn("dataset.tab.experiments", l3)).toBe(false);
    expect(
      surfaceShownIn("dataset.tab.experiments", {
        ...l3,
        experimentsBasic: true,
      }),
    ).toBe(true);
    // L5's dataset eval defaults only preselect New experiment evals.
    const l5 = { ...LAUNCH, madeReal: new Set(["dataset.experimentDefaults"]) };
    expect(surfaceShownIn("dataset.experimentDefaults", l5)).toBe(false);
    expect(
      surfaceShownIn("dataset.experimentDefaults", {
        ...l5,
        experimentsBasic: true,
      }),
    ).toBe(true);
    // L4's pin claim does not show L5's options, and the reverse.
    const l4 = { ...LAUNCH, madeReal: new Set(["dataset.evalVersions"]) };
    expect(surfaceShownIn("dataset.evalVersionNotice", l4)).toBe(false);
    expect(
      surfacesOfOption("ver:saved-list-notice").map((s) => s.id),
    ).not.toContain("dataset.evalVersions");
    // A child never shows under a hidden parent.
    const r = { ...LAUNCH, madeReal: new Set(["exp.new.runningSources"]) };
    expect(surfaceShownIn("exp.new.runningSources", r)).toBe(false);
  });

  it("each lane slot claims only known surfaces that name that lane", () => {
    Object.entries(MADE_REAL).forEach(([lane, ids]) =>
      ids.forEach((id) => {
        expect(SURFACE_BY_ID[id], `${lane} ${id}`).toBeTruthy();
        expect(SURFACE_BY_ID[id].madeRealBy, `${lane} ${id}`).toBe(lane);
      }),
    );
    Object.entries(MADE_REAL_PATHS).forEach(([lane, paths]) =>
      paths.forEach((p) =>
        expect(p.startsWith("/"), `${lane} ${p}`).toBe(true),
      ),
    );
  });

  it("gives every deep-link key the pages where it applies", () => {
    LAUNCH_SURFACES.forEach((s) =>
      (s.entries?.search ?? []).forEach((rule) => {
        expect(rule.on, `${s.id} ${rule.key}`).toBeInstanceOf(RegExp);
        expect(pageFor(rule.on), `${s.id} ${rule.key}`).toBeTruthy();
      }),
    );
  });
});

describe("L6 launch switch: no entry path reaches a hidden surface", () => {
  const hidden = LAUNCH_SURFACES.filter((s) => !surfaceShownIn(s.id, LAUNCH));

  it("routes land on their fallback, and a fallback is never hidden", () => {
    let checked = 0;
    hidden.forEach((s) =>
      (s.entries?.routes ?? []).forEach((r) => {
        const to = launchRedirect({ pathname: r.sample, search: "" }, LAUNCH);
        expect(to, `${s.id} ${r.sample}`).not.toBeNull();
        expect(to.surface).toBe(s.id);
        expect(launchRedirect({ pathname: to.to }, LAUNCH), to.to).toBeNull();
        checked += 1;
      }),
    );
    expect(checked).toBeGreaterThanOrEqual(7);
  });

  it("deep-link keys are dropped (or replaced) on their pages", () => {
    let checked = 0;
    hidden.forEach((s) =>
      (s.entries?.search ?? []).forEach((rule) => {
        const page = pageFor(rule.on);
        const value = rule.values?.[0] ?? "1";
        const q = `?${rule.key}=${encodeURIComponent(value)}&keep=1`;
        const out = sanitizeLaunchSearch(q, page, LAUNCH);
        expect(out.changed, `${s.id} ${rule.key}`).toBe(true);
        const p = new URLSearchParams(out.search);
        if (rule.replace !== undefined)
          expect(p.get(rule.key)).toBe(rule.replace);
        else expect(p.has(rule.key), `${s.id} ${rule.key}`).toBe(false);
        expect(p.get("keep")).toBe("1");
        checked += 1;
      }),
    );
    expect(checked).toBeGreaterThanOrEqual(15);
  });

  it("the named deep links do nothing in the default launch build", () => {
    const page = SAMPLE_PAGE.ON_DATASET_PAGE;
    [
      "?tab=data&add=production",
      "?tab=experiments&new_experiment=1&entry=http&rows=r1,r2",
      "?newExperiment=bakeoff-v2",
    ].forEach((q) => {
      const to = launchRedirect({ pathname: page, search: q }, LAUNCH);
      expect(to, q).not.toBeNull();
      const p = new URLSearchParams(to.to.split("?")[1] ?? "");
      // J09 REVISED: the classic drawer key survives; new scope keys do not.
      ["add", "newExperiment", "entry", "rows"].forEach((k) =>
        expect(p.has(k), `${q} ${k}`).toBe(false),
      );
    });
    // experimentsBasic keeps New experiment, never an Already running entry.
    const basic = sanitizeLaunchSearch(
      "?new_experiment=1&entry=http",
      page,
      BASIC,
    );
    expect(new URLSearchParams(basic.search).get("new_experiment")).toBe("1");
    expect(new URLSearchParams(basic.search).has("entry")).toBe(false);
    // The same key on another page is that page's own.
    expect(
      sanitizeLaunchSearch("?view=x", "/dashboard/observe/p1", LAUNCH).changed,
    ).toBe(false);
  });

  it("drops the entire reserved simulation query namespace, including repeated and encoded keys", () => {
    const query =
      "?proto_stage=r4&proto_stage=r5&proto_role=admin&proto_future=x&%70roto_encoded=y&keep=1&prototype=ordinary";
    for (const state of [LAUNCH, BASIC]) {
      for (const page of [
        SAMPLE_PAGE.ON_DATASET_PAGE,
        "/dashboard/observe/p1",
      ]) {
        const out = sanitizeLaunchSearch(query, page, state);
        expect(out.search).toBe("?keep=1&prototype=ordinary");
        expect(out.changed).toBe(true);
        expect(out.surfaces).toEqual(["shell.protoOnly"]);
        expect(out.dropped).toEqual([
          "proto_stage=r4",
          "proto_role=admin",
          "proto_future=x",
          "proto_encoded=y",
        ]);
        expect(sanitizeLaunchSearch(out.search, page, state).changed).toBe(
          false,
        );
      }
    }
    const out = sanitizeLaunchSearch(query, SAMPLE_PAGE.ON_DATASET_PAGE, PROTO);
    expect(out.changed).toBe(false);
    expect(new URLSearchParams(out.search).getAll("proto_stage")).toEqual([
      "r4",
      "r5",
    ]);
    expect(surfacesOfOption("url:proto_stage").map((s) => s.id)).toContain(
      "shell.protoOnly",
    );
  });

  it("palette verbs of hidden surfaces never show", () => {
    let checked = 0;
    hidden.forEach((s) =>
      (s.entries?.verbs ?? []).forEach((g) => {
        expect(verbShownIn(sampleOf(g), LAUNCH), `${s.id} ${g}`).toBe(false);
        checked += 1;
      }),
    );
    expect(checked).toBeGreaterThanOrEqual(10);
    ["view.copyLink", "theme.toggle", "go.datasets", "dataset.copyId"].forEach(
      (id) => expect(verbShownIn(id, LAUNCH), id).toBe(true),
    );
  });

  it("shortcuts of hidden surfaces never fire or list", () => {
    let checked = 0;
    hidden.forEach((s) =>
      (s.entries?.shortcuts ?? []).forEach((k) => {
        const [surface, action] = k.split(":");
        expect(
          shortcutShownIn(surface, sampleOf(action), LAUNCH),
          `${s.id} ${k}`,
        ).toBe(false);
        checked += 1;
      }),
    );
    expect(checked).toBeGreaterThanOrEqual(10);
    expect(shortcutShownIn("grid", "filter", LAUNCH)).toBe(true);
    expect(shortcutShownIn("global", "palette", LAUNCH)).toBe(true);
  });

  it("hidden dataset tabs never render; the glossary keeps only shown terms", () => {
    // J09 REVISED: the classic tab is independent of the DSX leaderboard.
    expect(datasetTabShownIn("experiments", LAUNCH)).toBe(true);
    // Census §A: the legacy Evals/Prompt summary survives independently of R5.
    expect(datasetTabShownIn("summary", LAUNCH)).toBe(true);
    expect(datasetTabShownIn("data", LAUNCH)).toBe(true);
    expect(datasetTabShownIn("optimization", LAUNCH)).toBe(true);
    expect(glossaryShownIn("baseline", LAUNCH)).toBe(false);
    expect(glossaryShownIn("flaky", LAUNCH)).toBe(false);
    expect(glossaryShownIn("wrapLines", LAUNCH)).toBe(true);
  });
});

describe("L6 launch switch: every query key the dataset page reads", () => {
  // Derived from the source (datasetPageKeys.js), never a hand list: a key
  // the page reads must open only shown surfaces (LAUNCH_DATASET_PAGE_KEYS),
  // be dropped by a hidden surface's rule, or be read only inside a host
  // that never mounts in the default launch build.
  const HIDDEN_HOSTS = [
    ["sections/develop-detail/ExperimentTab/", "dataset.tab.experiments"],
    ["sections/develop-detail/DatasetSummaryTab/", "dataset.tab.summary"],
    // J09: the prompt wizard mounts through exp.classic, so its query reads
    // are no longer exempt as a hidden host.
    [
      "sections/develop-detail/DataTab/DatapointDrawerV2/RowRunsTab.jsx",
      "dataset.rowExtras.runs",
    ],
    [
      "sections/develop-detail/DataTab/DatapointDrawerV2/RowActivityTab.jsx",
      "dataset.rowTab.activity",
    ],
  ];
  const hostOf = (file) => HIDDEN_HOSTS.find(([p]) => file.startsWith(p));
  const { reads, dynamic } = scanDatasetPageKeys(resolve(process.cwd(), "src"));
  const page = SAMPLE_PAGE.ON_DATASET_PAGE;
  const ruleFor = (key) =>
    LAUNCH_SURFACES.filter((s) => !surfaceShownIn(s.id, LAUNCH)).flatMap((s) =>
      (s.entries?.search ?? [])
        .filter((r) => r.key === key && r.on.test(page))
        .map((r) => ({ surface: s.id, rule: r })),
    );

  it("finds the keys the verify walk opened (the scan is not empty)", () => {
    const keys = new Set(reads.map((r) => r.key));
    [
      "history",
      "version",
      "splits",
      "rowTab",
      "ract",
      "rcs",
      "rcompose",
      "row",
      "tab",
    ].forEach((k) => expect(keys, k).toContain(k));
    expect(ON_DATASET_PAGE.test(page)).toBe(true);
  });

  it("every key outside a hidden host is shown or dropped by a hidden surface", () => {
    const open = reads
      .filter((r) => !hostOf(r.file))
      .filter((r) => !(r.key in LAUNCH_DATASET_PAGE_KEYS))
      .filter((r) => ruleFor(r.key).length === 0)
      .map((r) => `${r.key} (${r.file} ${r.how})`);
    expect(open).toEqual([]);
  });

  it("a cold deep link with each hidden key is dropped on the dataset page", () => {
    const hiddenKeys = [
      ...new Set(
        reads.map((r) => r.key).filter((k) => !(k in LAUNCH_DATASET_PAGE_KEYS)),
      ),
    ].filter((k) => ruleFor(k).length > 0);
    expect(hiddenKeys.length).toBeGreaterThanOrEqual(15);
    hiddenKeys.forEach((key) => {
      const { rule } = ruleFor(key)[0];
      const value = rule.values?.[0] ?? "2";
      const to = launchRedirect(
        { pathname: page, search: `?tab=data&${key}=${value}` },
        LAUNCH,
      );
      expect(to, key).not.toBeNull();
      const p = new URLSearchParams(to.to.split("?")[1] ?? "");
      if (rule.replace !== undefined) expect(p.get(key)).toBe(rule.replace);
      else expect(p.has(key), key).toBe(false);
      expect(p.get("tab")).toBe("data");
    });
  });

  it("the verify walk's links: history, version, splits and the comment door", () => {
    [
      "?tab=data&history=1",
      "?tab=data&version=2",
      "?tab=data&splits=hard",
      "?tab=data&row=r1&rowTab=activity&ract=comments&rcs=open&rcompose=1",
      "?tab=data&row=r1&rowTab=runs&run=x1",
    ].forEach((q) => {
      const to = launchRedirect({ pathname: page, search: q }, LAUNCH);
      expect(to, q).not.toBeNull();
      const p = new URLSearchParams(to.to.split("?")[1] ?? "");
      [
        "history",
        "version",
        "splits",
        "rowTab",
        "ract",
        "rcs",
        "rcompose",
        "run",
      ].forEach((k) => expect(p.has(k), `${q} ${k}`).toBe(false));
    });
    // The Row and Raw tabs and the row itself stay.
    expect(
      launchRedirect(
        { pathname: page, search: "?tab=data&row=r1&rowTab=raw" },
        LAUNCH,
      ),
    ).toBeNull();
    ROW_EXTRA_TABS.forEach((t) =>
      expect(
        sanitizeLaunchSearch(`?rowTab=${t}`, page, LAUNCH).changed,
        t,
      ).toBe(true),
    );
  });

  it("dynamic reads stay in hidden hosts except the bounded classic picker reader", () => {
    // J09: this one helper now mounts. Pin every literal caller key instead of
    // retaining the old blanket hidden-host exemption for all RunSources files.
    const pickerFile = "sections/common/RunSources/sourceModel.js";
    const pickerReads = dynamic.filter((d) => d.file === pickerFile);
    expect(pickerReads).toEqual([{ file: pickerFile, expr: "params.get(k)" }]);
    const pickerSource = readFileSync(
      resolve(process.cwd(), "src", pickerFile),
      "utf8",
    );
    const keys = [
      ...new Set(
        [...pickerSource.matchAll(/\bget\("([a-z_]+)"\)/g)].map(
          (match) => match[1],
        ),
      ),
    ].sort();
    expect(keys).toEqual([
      "agent",
      "entry",
      "form",
      "lang",
      "mode",
      "new_experiment",
      "source",
      "src",
      "view",
    ]);
    for (const key of keys)
      expect(
        key in LAUNCH_DATASET_PAGE_KEYS || ruleFor(key).length > 0,
        key,
      ).toBe(true);
    const stray = dynamic.filter((d) => {
      if (d.file === pickerFile && d.expr === "params.get(k)") return false;
      const h = hostOf(d.file);
      return !h || surfaceShownIn(h[1], LAUNCH);
    });
    expect(stray).toEqual([]);
    HIDDEN_HOSTS.forEach(([, id]) =>
      expect(surfaceShownIn(id, LAUNCH), id).toBe(false),
    );
  });

  it("lists no shown key the page no longer reads", () => {
    const keys = new Set(reads.map((r) => r.key));
    Object.keys(LAUNCH_DATASET_PAGE_KEYS).forEach((k) =>
      expect(keys, k).toContain(k),
    );
  });
});

describe("L6 launch switch: the verify round's doors", () => {
  it("hides the palette's Import rows, Add row and production-traces verbs", () => {
    ["grid.importRows", "grid.addRow", "datasets.fromTraces"].forEach((id) =>
      expect(verbShownIn(id, LAUNCH), id).toBe(false),
    );
    ["grid.importRows", "grid.addRow", "datasets.fromTraces"].forEach((id) =>
      expect(verbShownIn(id, PROTO), id).toBe(true),
    );
  });

  it("the Add rows sheet retains legacy doors but keeps NEW paste gated", () => {
    expect(
      addRowsTilesIn(ADD_ROWS_OPTIONS, LAUNCH).map(({ id }) => id),
    ).toEqual([
      "uploadFile",
      "addFromExistingDataset",
      "importFromHuggingface",
      "empty-row",
    ]);
    expect(
      addRowsTilesIn(ROW_IMPORT_OPTIONS, LAUNCH).map(({ id }) => id),
    ).toEqual([
      "empty-row",
      "uploadFile",
      "addFromExistingDataset",
      "importFromHuggingface",
    ]);
    expect(addRowsTilesIn(ADD_ROWS_OPTIONS, PROTO)).toHaveLength(
      ADD_ROWS_OPTIONS.length,
    );
    // Every tile names a known surface; an unknown tile never shows.
    [...ADD_ROWS_OPTIONS, ...ROW_IMPORT_OPTIONS].forEach((o) =>
      expect(SURFACE_BY_ID[ADD_ROWS_TILE_SURFACE[o.id]], o.id).toBeTruthy(),
    );
    expect(addRowsTilesIn([{ id: "nope" }], LAUNCH)).toEqual([]);
    const l5 = { ...LAUNCH, madeReal: new Set(["hf.addToExisting"]) };
    expect(addRowsTilesIn(ADD_ROWS_OPTIONS, l5).map(({ id }) => id)).toEqual([
      "uploadFile",
      "addFromExistingDataset",
      "importFromHuggingface",
      "empty-row",
    ]);
    expect(ADD_ROWS_TILE_SURFACE.paste).toBe("dataset.imports.pasteRows");
    expect(surfaceShownIn("dataset.imports.pasteRows", LAUNCH)).toBe(false);
  });
});

describe("L6 launch switch: verify 2's latent palette verbs", () => {
  const read = (p) => readFileSync(resolve(process.cwd(), "src", p), "utf8");
  // Each verb id, and the file that registers it today (so the exclusion is
  // never for an id nobody registers).
  const LATENT = [
    ["row.history", "dataset.versions", "RowView.jsx"],
    ["row.saveVersion", "dataset.versions", "RowView.jsx"],
    ["version.restore", "dataset.versions", "AsOfBanner.jsx"],
    ["version.changes", "dataset.versions", "AsOfBanner.jsx"],
    ["version.latest", "dataset.versions", "AsOfBanner.jsx"],
    [
      "selection.restoreRows",
      "dataset.versions",
      "DevelopDataSelectionActive.jsx",
    ],
    ["row.splits", "dataset.splits", "RowView.jsx"],
  ];
  const FILES = {
    "RowView.jsx":
      "sections/develop-detail/DataTab/DatapointDrawerV2/RowView.jsx",
    "AsOfBanner.jsx": "sections/develop-detail/DataTab/Versions/AsOfBanner.jsx",
    "DevelopDataSelectionActive.jsx":
      "sections/develop-detail/DevelopBarRightSection/DevelopDataSelectionActive.jsx",
  };

  it("never show in a launch build, flag OFF or ON; a prototype keeps them", () => {
    LATENT.forEach(([id, surface, file]) => {
      expect(read(FILES[file]), `${file} registers ${id}`).toContain(
        `id: "${id}"`,
      );
      expect(
        SURFACE_BY_ID[surface].entries.verbs.some((g) => matchesGlob(g, id)),
        `${surface} lists ${id}`,
      ).toBe(true);
      expect(verbShownIn(id, LAUNCH), `${id} OFF`).toBe(false);
      expect(verbShownIn(id, BASIC), `${id} ON`).toBe(false);
      expect(verbShownIn(id, PROTO), `${id} prototype`).toBe(true);
    });
  });

  it("the Row view's other verbs stay (positive control)", () => {
    ["row.copyJson", "row.full", "row.duplicate", "row.delete"].forEach(
      (id) => {
        expect(verbShownIn(id, LAUNCH), id).toBe(true);
        expect(verbShownIn(id, BASIC), id).toBe(true);
      },
    );
  });
});

describe("L6 launch switch: wiring", () => {
  const read = (p) => readFileSync(resolve(process.cwd(), p), "utf8");

  it("the app shell mounts the Rerun with changes host only where exp.rerun ships", () => {
    const shell = read("src/layouts/dashboard/index.jsx");
    expect(shell).toMatch(
      /return isSurfaceShown\("exp\.rerun"\) && \(datasetId \|\| experimentId\)\s*\?\s*<RerunWithChangesHost \/>\s*:\s*null;/,
    );
    expect(shell).toMatch(/<RerunHostMount \/>/);
    expect(shell.match(/<RerunWithChangesHost \/>/g)).toHaveLength(1);
    expect(surfaceShownIn("exp.rerun", LAUNCH)).toBe(false);
    expect(surfaceShownIn("exp.rerun", BASIC)).toBe(false);
  });

  it("no New experiment door preselects rows while dev runs every row (flag ON too)", () => {
    const rowView = read(
      "src/sections/develop-detail/DataTab/DatapointDrawerV2/RowView.jsx",
    );
    expect(rowView).toMatch(
      /isSurfaceShown\("exp\.new\.scope"\)\s*\?\s*\{\s*newExperiment:/,
    );
    const body = read(
      "src/sections/develop-detail/DataTab/DatapointDrawerV2/RowViewBody.jsx",
    );
    expect(body).toMatch(
      /!isSurfaceShown\("exp\.new\.scope"\)\s*\?\s*undefined\s*:\s*actions\.newExperiment/,
    );
    expect(surfaceShownIn("exp.new.scope", BASIC)).toBe(false);
    expect(surfaceShownIn("exp.new.scope", PROTO)).toBe(true);
  });

  it("main.jsx installs the absent-path guard once; the route guard reads launchRedirect", () => {
    expect(read("src/main.jsx")).toMatch(
      /import "\.\/launch\/installLaunchGuard";/,
    );
    const routes = read("src/routes/sections/dashboard.jsx");
    expect(routes).toMatch(
      /const launchTo = PROTOTYPE_BUILD \? null : launchRedirect\(location\);/,
    );
    expect(routes).toMatch(
      /launchTo \? \(\s*(\/\/[^\n]*\n\s*)*<Navigate to=\{launchTo\.to\} replace \/>/,
    );
  });

  it("J09 REVISED: the classic prompt drawer mounts independently of NEW experiment children", () => {
    const view = read("src/sections/develop-detail/DevelopDetailView.jsx");
    expect(view).toMatch(
      /isSurfaceShown\("exp\.classic"\) \? \(\s*<RunExperimentWithProvider/,
    );
    expect(view).toMatch(/isDatasetTabShown\(urlTab\)/);
  });
});
