/* eslint-disable react/prop-types */
// Observe first-run setup at 1024 px: instrumentation samples sit in a
// half-width card and their long lines ran past the code box. The sample now
// wraps inside its box (indentation kept) and leaves room for the copy button.
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render as plainRender } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import InstructionCodeCopy from "../InstructionCodeCopy";
import ObserveInstruments from "../ObserveInstuments";

// Keep the real module, but render the highlighter as a plain pre/code pair
// that carries the styles the component passes in.
vi.mock("react-syntax-highlighter", async (importOriginal) => ({
  ...(await importOriginal()),
  Prism: ({ children, customStyle, codeTagProps }) => (
    <pre style={customStyle}>
      <code {...codeTagProps}>{children}</code>
    </pre>
  ),
}));
vi.mock("src/components/iconify", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => <span />,
}));
vi.mock("src/components/snackbar", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));

const longLine =
  "LangChainInstrumentor().instrument(tracer_provider=trace_provider)  # a long setup line";

describe("setup code sample layout", () => {
  it("wraps long lines inside the code box and keeps the text whole", () => {
    const { container } = plainRender(
      <InstructionCodeCopy text={longLine} language="python" />,
    );
    const pre = container.querySelector("pre");
    const code = container.querySelector("pre code");
    expect(pre.style.whiteSpace).toBe("pre-wrap");
    expect(code.style.whiteSpace).toBe("pre-wrap");
    // The box still scrolls if a single token cannot break.
    expect(pre.style.overflowX).toBe("auto");
    expect(code.textContent).toBe(longLine);
  });

  it("keeps the first line clear of the copy button", () => {
    const { container } = plainRender(
      <InstructionCodeCopy text={longLine} language="python" />,
    );
    expect(container.querySelector("pre").style.paddingRight).toBe("40px");
  });

  it("wraps instrumentation samples inside their card", () => {
    const { container } = render(
      <ObserveInstruments
        isSuccess
        languageTab="python"
        onLanguageChange={vi.fn()}
        data={{
          langchain: {
            name: "LangChain",
            Python: { code: longLine },
          },
        }}
      />,
    );
    const code = container.querySelector("pre code");
    expect(code).not.toBeNull();
    expect(code).toHaveStyle({ whiteSpace: "pre-wrap" });
    expect(container.querySelector("pre")).toHaveStyle({
      whiteSpace: "pre-wrap",
    });
    expect(code.textContent).toBe(longLine);
  });
});
