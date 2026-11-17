/* eslint-disable react/prop-types -- Test-only harness. */
// The screens whose model menus list the decision catalog beyond the native
// Jev models. Jev is listed on every screen; the rest of the catalog only
// where the launch switch shows decision models and the screen lists them
// (DecisionJudgesContext). A model selector with no screen around it lists
// the catalog. Every screen lists it, as the team's code does on each screen
// it has; a screen providing false would list only Jev. The screens are too
// wide to mount here, so the value each one provides is read from its source
// (the pages' own menus are mounted in decision-catalog-pages.test.jsx); the
// selector itself is mounted.
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import axios, { endpoints } from "src/utils/axios";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { DecisionJudgesContext } from "src/sections/common/DecisionJudges/DecisionJudgesContext";
import ModelSelector from "../ModelSelector";

vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { get: vi.fn(), post: vi.fn() },
}));
vi.mock("src/hooks/useCapabilities", async (original) => ({
  ...(await original()),
  useFeatureLocked: () => ({ locked: false, isLoading: false }),
  useFeatureDenied: () => false,
  useFeatureAllowed: () => ({ allowed: false }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/form-code-editor", () => ({
  FormCodeEditor: () => null,
}));

const SECTIONS = join(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "..",
);
const SRC = join(SECTIONS, "..");
const source = (file) => readFileSync(join(SECTIONS, file), "utf8");

// Every file that renders the model selector, by its path under sections/.
const RENDERERS = [
  "common/EvalPicker/EvalPickerConfig.jsx",
  "evals/alignment/ComparePanel.jsx",
  "evals/alignment/TunedJudgeCheck.jsx",
  "evals/components/InstructionEditor.jsx",
  "evals/components/MessageEditor.jsx",
  "evals/jury/JurySeatEditor.jsx",
  "evals/types/CompositeChildControls.jsx",
  "evals/types/DagNodeCard.jsx",
];

// Every screen that renders the selectors inside it, how it lists the
// catalog, and the values its own providers give.
const HOSTS = [
  ["common/EvalPicker/EvalPickerConfigFull.jsx", "provides true", [true]],
  ["evals/alignment/ComparePanel.jsx", "provides true", [true]],
  ["evals/alignment/TunedJudgeCheck.jsx", "provides true", [true]],
  [
    "common/EvalPicker/EvalPickerCreateNew.jsx",
    "provides no value (the default, true)",
    [],
  ],
  [
    "common/EvalPicker/EvalPickerConfig.jsx",
    "provides no value (the default, true)",
    [],
  ],
  [
    "evals/components/EvalCreatePage.jsx",
    "provides no value (the default, true)",
    [],
  ],
  [
    "evals/components/EvalDetailPage.jsx",
    "provides no value (the default, true)",
    [],
  ],
];

// The renderers with no value of their own follow the screen that renders
// them: the instruction and prompt editors (the full picker, Create New and
// the two eval pages), the jury seats and composite children (Create New and
// the two eval pages), and the decision-tree nodes (the full picker and the
// two eval pages).
const FOLLOWERS = RENDERERS.filter(
  (file) => !HOSTS.some(([host]) => host === file),
);

const IMPORTS_SELECTOR =
  /import ModelSelector\b[^;]*from "[^"]*\/ModelSelector";/;
const RENDERS_SELECTOR = /<ModelSelector\b/;

function sourceFiles(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory())
      return ["__tests__", "__fixtures__", "__mocks__"].includes(entry.name)
        ? []
        : sourceFiles(path);
    return /\.(jsx?|tsx?)$/.test(entry.name) &&
      !/\.(test|spec|stories)\./.test(entry.name)
      ? [path]
      : [];
  });
}

// The value of each DecisionJudgesContext provider in a file (a bare `value`
// is true).
const providedValues = (text) =>
  [
    ...text.matchAll(
      /<DecisionJudgesContext\.Provider\s+value(?:=\{(true|false)\})?\s*>/g,
    ),
  ].map(([, value]) => value !== "false");

describe("the screens that list the decision catalog", () => {
  it("names every file that renders the model selector", () => {
    const found = sourceFiles(SRC)
      .filter((path) => {
        const text = readFileSync(path, "utf8");
        return IMPORTS_SELECTOR.test(text) && RENDERS_SELECTOR.test(text);
      })
      .map((path) => relative(SECTIONS, path).split(sep).join("/"))
      .sort();
    expect(found).toEqual([...RENDERERS].sort());
  });

  it.each(HOSTS)("%s %s", (file, _how, values) => {
    expect(providedValues(source(file))).toEqual(values);
  });

  it("has no screen that lists only the native Jev models", () => {
    const listsOnlyJev = sourceFiles(SRC)
      .filter((path) =>
        providedValues(readFileSync(path, "utf8")).includes(false),
      )
      .map((path) => relative(SECTIONS, path).split(sep).join("/"));
    expect(listsOnlyJev).toEqual([]);
  });

  it.each(FOLLOWERS)("%s follows the screen that renders it", (file) => {
    expect(source(file)).not.toContain("DecisionJudgesContext");
  });
});

const connection = (model_name, provider, label) => ({
  model_name,
  provider,
  label,
  is_available: true,
});
const group = (model_name, label, author, connections) => ({
  model_name,
  label,
  author,
  connections,
  question_types: ["noul", "choice", "score"],
  type: "decision",
  version: "versioned",
  min_choices: 1,
  max_choices: 255,
  max_state_bytes: 120000,
  unavailable_reason: null,
  notice: "Text and JSON inputs only.",
});
const CATALOG = [
  group("typesafe/jev-1.14", "Jev 1.14", "TypeSafe", [
    connection("jev-1.14.0", "typesafe", "TypeSafe"),
    connection(
      "decision/openrouter/typesafe/jev-1.14",
      "openrouter",
      "OpenRouter",
    ),
  ]),
  group("openai/gpt-6-luna-decisions", "GPT-6 Luna Decisions", "OpenAI", [
    connection("decision/openai/gpt-6-luna", "openai", "OpenAI"),
  ]),
];

beforeEach(() => {
  vi.clearAllMocks();
  axios.get.mockImplementation(async (url, { params } = {}) => {
    if (url === endpoints.develop.apiKey.status)
      return {
        data: {
          result: {
            providers: [
              {
                provider: "typesafe",
                display_name: "TypeSafe",
                type: "text",
                has_key: true,
              },
              {
                provider: "openai",
                display_name: "OpenAI",
                type: "text",
                has_key: true,
              },
            ],
          },
        },
      };
    if (url === endpoints.develop.modelList)
      return {
        data:
          params?.model_type === "decision"
            ? {
                count: CATALOG.length,
                results: CATALOG,
                next: null,
                current_page: 1,
              }
            : {
                count: 1,
                results: [
                  { model_name: "ordinary-model", providers: "custom" },
                ],
                next: null,
                current_page: 1,
              },
      };
    return { data: { results: [], result: { table_data: [], total_rows: 0 } } };
  });
});
afterEach(() => setLaunchOverrideForTest(null));

// `screenValue` is the value the screen around the selector provides;
// undefined mounts the selector with no screen around it.
const mount = (screenValue) => {
  const selector = (
    <ModelSelector model="ordinary-model" onModelChange={vi.fn()} />
  );
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      {screenValue === undefined ? (
        selector
      ) : (
        <DecisionJudgesContext.Provider value={screenValue}>
          {selector}
        </DecisionJudgesContext.Provider>
      )}
    </QueryClientProvider>,
  );
};

async function openModelMenu() {
  await userEvent.click(
    screen.getByRole("button", { name: /^Evaluation model:/ }),
  );
  // Jev 1.14 comes only from the catalog, so the catalog has loaded.
  await screen.findByRole("menuitem", { name: /Jev 1\.14/ });
}

const lunaRow = () =>
  screen.queryByRole("menuitem", { name: /GPT-6 Luna Decisions/ });

describe("the decision catalog in the model menu", () => {
  it("lists the whole catalog with no screen around the selector", async () => {
    mount();
    await openModelMenu();
    expect(lunaRow()).toBeInTheDocument();
  });

  it("lists the whole catalog on a screen that provides true", async () => {
    mount(true);
    await openModelMenu();
    expect(lunaRow()).toBeInTheDocument();
  });

  it("lists only the native Jev models on a screen that provides false", async () => {
    mount(false);
    await openModelMenu();
    expect(
      screen.getByRole("menuitem", { name: /Jev 1\.14/ }),
    ).toBeInTheDocument();
    expect(lunaRow()).not.toBeInTheDocument();
  });

  it("lists only the native Jev models where the launch switch hides decision models", async () => {
    setLaunchOverrideForTest({
      launch: true,
      showUnreleased: false,
      experimentsBasic: false,
      madeReal: [],
    });
    mount(true);
    await openModelMenu();
    expect(lunaRow()).not.toBeInTheDocument();
  });
});
