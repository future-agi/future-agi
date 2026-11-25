import React from "react";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import CodeInput from "src/sections/develop-detail/Common/EvaluationConfigure/CodeInput";
import InstructionCodeCopy from "src/sections/project/NewProject/InstructionCodeCopy";
import MetricEmptyState from "src/sections/workbench/createPrompt/Metrics/MetricEmptyState";

const mocks = vi.hoisted(() => ({
  copy: vi.fn(),
  snackbar: vi.fn(),
  snippets: {
    python: 'message = f"Hello {name}"\nprint(message)',
    typescript: 'const message: string = "Hello";\nconsole.log(message);',
  },
}));

vi.mock("src/utils/utils", () => ({
  copyToClipboard: mocks.copy,
  camelCaseToTitleCase: (value: string) => value,
}));
vi.mock("src/components/snackbar", () => ({
  enqueueSnackbar: mocks.snackbar,
}));
vi.mock("notistack", () => ({ enqueueSnackbar: mocks.snackbar }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("@monaco-editor/react", () => ({ default: () => null }));
vi.mock("react-hook-form", () => ({
  useController: () => ({
    field: { value: "", onChange: vi.fn() },
    formState: { errors: {} },
  }),
}));
vi.mock("src/sections/develop-detail/Common/HelperText", () => ({
  default: () => null,
}));
vi.mock("src/utils/axios", () => ({ default: {}, endpoints: {} }));
vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({ data: mocks.snippets, isLoading: false }),
}));

const codeMarkup = (container: HTMLElement) =>
  container.querySelector("pre code")!.innerHTML;

type Highlighter = React.ComponentType<{
  language: string;
  useInlineStyles: boolean;
  children: string;
}>;

const require = createRequire(import.meta.url);

// A fresh Node process loads only the full renderer, never the candidate's
// refractor singleton. Importing the full build in this test process is not an
// independent oracle: earlier light registrations would silently win.
const fullMarkup = (language: string, text: string, prism = true) => {
  const html = execFileSync(
    process.execPath,
    [
      "--input-type=commonjs",
      "-e",
      `const React = require(${JSON.stringify(require.resolve("react"))});
       const { renderToStaticMarkup } = require(${JSON.stringify(require.resolve("react-dom/server"))});
       const full = require(${JSON.stringify(require.resolve("react-syntax-highlighter"))});
       const { language, text, prism } = JSON.parse(require("node:fs").readFileSync(0, "utf8"));
       process.stdout.write(renderToStaticMarkup(React.createElement(
         prism ? full.Prism : full.default,
         { language, useInlineStyles: false }, text)));`,
    ],
    {
      input: JSON.stringify({ language, text, prism }),
      encoding: "utf8",
    },
  );
  const container = document.createElement("div");
  container.innerHTML = html;
  return codeMarkup(container);
};

const typescriptCases = [
  mocks.snippets.typescript,
  'import { Client } from "client";\nexport const run = async () => {\n  const values: Array<string> = [...items];\n  if (values.length) return Promise.resolve(values.map(String));\n};',
  "/**\n * @param {string} name - display name\n * @returns {Promise<string>} message\n */\nfunction greet<T extends string>(name: T): Promise<T> { return Promise.resolve(name); }",
  'const message = `Hello ${name.toUpperCase()}`;\nconst style = css`a.link:hover { color: red; }`;\nconst view = html`<b class="value">${message}</b>`;\nconst icon = svg`<path fill="red" />`;\nconst query = gql`query { user { id } }`;\nconst rows = sql`SELECT id FROM users WHERE id = ${id}`;',
  "const t = html`<script>/** @param {string} name */ console.log(name);</script>`;",
];

afterEach(cleanup);
beforeEach(() => vi.clearAllMocks());

describe("light syntax highlighter builds", () => {
  it("registers the snippet languages before any full build is loaded", () => {
    const { container } = render(
      <>
        <InstructionCodeCopy text={mocks.snippets.python} language="python" />
        <InstructionCodeCopy
          text={mocks.snippets.typescript}
          language="typescript"
        />
        <CodeInput control={{}} config={{}} configKey="code" allColumns={[]} />
      </>,
    );
    const blocks = container.querySelectorAll("pre code");
    expect(blocks[0].querySelector(".token.string")).not.toBeNull();
    expect(blocks[1].querySelector(".token.keyword")).not.toBeNull();
    expect(blocks[2].querySelector(".hljs-comment")).not.toBeNull();
  });

  it.each(["python", "typescript"] as const)(
    "keeps the instruction %s tokens and copy behavior",
    async (language) => {
      const text = mocks.snippets[language];
      const expected = await fullMarkup(language, text);
      const onCopy = vi.fn();
      const { container } = render(
        <InstructionCodeCopy text={text} language={language} onCopy={onCopy} />,
      );

      expect(codeMarkup(container)).toBe(expected);
      expect(container.querySelector("pre code")).toHaveTextContent(
        text.replace(/\s+/g, " "),
      );
      expect(container.querySelector("pre")).toHaveStyle({
        fontSize: "12px",
        whiteSpace: "pre",
        overflowX: "auto",
      });
      fireEvent.click(screen.getByRole("button"));
      expect(mocks.copy).toHaveBeenCalledWith(text);
      expect(mocks.snackbar).toHaveBeenCalledWith("Copied to clipboard", {
        variant: "success",
      });
      expect(onCopy).toHaveBeenCalledOnce();
    },
  );

  it.each(typescriptCases)(
    "matches isolated full-build TypeScript markup before importing full Prism for %s",
    (text) => {
      const { container } = render(
        <InstructionCodeCopy text={text} language="typescript" />,
      );
      expect(codeMarkup(container)).toBe(fullMarkup("typescript", text));
    },
  );

  it.each([undefined, "java", "javascript", "unknown"])(
    "renders unsupported instruction language %s as plain text",
    (language) => {
      const text = 'class Example { String value = "<example>"; }';
      const { container } = render(
        <InstructionCodeCopy text={text} language={language} />,
      );
      const code = container.querySelector("pre code")!;
      expect(code.textContent).toBe(text);
      expect(code.querySelector(".token")).toBeNull();
      expect(code.querySelector("example")).toBeNull();
    },
  );

  it("keeps the evaluation helper's Python highlight.js tokens", async () => {
    const text =
      '# Use f-string to access column values\n\nvar = f"{{col_name}}"';
    const expected = await fullMarkup("python", text, false);
    const { container } = render(
      <CodeInput control={{}} config={{}} configKey="code" allColumns={[]} />,
    );
    expect(codeMarkup(container)).toBe(expected);
    expect(container.querySelector("pre")).toHaveClass("hljs");
    expect(container.querySelector(".hljs-comment")).not.toBeNull();
  });

  it("keeps metrics highlighting and copying on both language tabs", async () => {
    const expectedPython = await fullMarkup("python", mocks.snippets.python);
    const expectedTypeScript = await fullMarkup(
      "typescript",
      mocks.snippets.typescript,
    );
    const { container } = render(<MetricEmptyState />);

    expect(codeMarkup(container)).toBe(expectedPython);
    fireEvent.click(screen.getByRole("button"));
    expect(mocks.copy).toHaveBeenLastCalledWith(mocks.snippets.python);

    fireEvent.click(screen.getByRole("tab", { name: "TypeScript" }));
    expect(codeMarkup(container)).toBe(expectedTypeScript);
    fireEvent.click(screen.getByRole("button"));
    expect(mocks.copy).toHaveBeenLastCalledWith(mocks.snippets.typescript);
  });

  it("preserves tokens when a full-Prism route loads after the light route", async () => {
    const { Prism: FullPrism } = await vi.importActual<{ Prism: Highlighter }>(
      "react-syntax-highlighter",
    );
    for (const text of typescriptCases) {
      for (const language of ["typescript", "javascript"]) {
        const view = render(
          <FullPrism useInlineStyles={false} language={language}>
            {text}
          </FullPrism>,
        );
        expect(codeMarkup(view.container)).toBe(fullMarkup(language, text));
        view.unmount();
      }
      const view = render(
        <InstructionCodeCopy text={text} language="typescript" />,
      );
      expect(codeMarkup(view.container)).toBe(fullMarkup("typescript", text));
      view.unmount();
    }
  });
});
