import React from "react";
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

const fullMarkup = async (language: string, text: string, prism = true) => {
  const { default: FullHighlighter, Prism: FullPrism } = await vi.importActual<{
    default: Highlighter;
    Prism: Highlighter;
  }>("react-syntax-highlighter");
  const Highlighter = prism ? FullPrism : FullHighlighter;
  const view = render(
    <Highlighter useInlineStyles={false} language={language}>
      {text}
    </Highlighter>,
  );
  const markup = codeMarkup(view.container);
  view.unmount();
  return markup;
};

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
});
