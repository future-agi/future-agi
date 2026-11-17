import React from "react";
import { describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import CodeEvalEditor, {
  JS_CODE_TEMPLATE,
  PYTHON_CODE_TEMPLATE,
} from "../CodeEvalEditor";

// Monaco does not run in jsdom.
vi.mock("../CodeEditor", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { post: vi.fn() },
}));

// Finished evals that begin the way the starters do.
const DEF_EVAL = `def evaluate(input, output, expected, context, **kwargs):
    words = str(output).split()
    return {"score": 1.0 if len(words) <= 50 else 0.0, "reason": "word limit"}`;

const TYPING_EVAL = `from typing import Any

def evaluate(input: Any, output: Any, expected: Any, context: dict, **kwargs):
    return 1.0 if "refund" in str(output).lower() else 0.0`;

const FUNCTION_EVAL = `function evaluate({ output }) {
  return String(output).toLowerCase().includes("refund") ? 1 : 0;
}`;

const RETURN_EVAL = `// Return: 1 when the answer gives the refund window
function evaluate({ output }) {
  return String(output).includes("30 days") ? 1 : 0;
}`;

const REPLACE_BODY =
  "Your current code will be replaced. The run's language is detected from the code, so you can keep your code and only change the language setting.";

const replaceTitle = (language) =>
  `Replace your code with the ${language} starter?`;

function renderEditor(code, codeLanguage = "python", disabled = false) {
  const setCode = vi.fn();
  const setCodeLanguage = vi.fn();
  render(
    <CodeEvalEditor
      code={code}
      setCode={setCode}
      codeLanguage={codeLanguage}
      setCodeLanguage={setCodeLanguage}
      disabled={disabled}
    />,
  );
  return { setCode, setCodeLanguage };
}

function chooseLanguage(name) {
  fireEvent.mouseDown(screen.getByRole("combobox"));
  fireEvent.click(screen.getByRole("option", { name }));
}

function findReplaceDialog(language) {
  return screen.findByRole("dialog", { name: replaceTitle(language) });
}

function choose(dialog, name) {
  fireEvent.click(within(dialog).getByRole("button", { name }));
}

async function waitForDialogToClose() {
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
}

describe("CodeEvalEditor language select and the user's code", () => {
  it.each([
    ["empty", ""],
    ["only whitespace", "  \n\t  "],
  ])("puts in the JavaScript starter when the code is %s", (_, code) => {
    const { setCode, setCodeLanguage } = renderEditor(code);

    chooseLanguage("JavaScript");

    expect(setCodeLanguage).toHaveBeenCalledWith("javascript");
    expect(setCode).toHaveBeenCalledTimes(1);
    expect(setCode).toHaveBeenCalledWith(JS_CODE_TEMPLATE);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it.each([
    [
      "the Python starter",
      PYTHON_CODE_TEMPLATE,
      "python",
      "JavaScript",
      JS_CODE_TEMPLATE,
    ],
    [
      "the Python starter with blank lines around it",
      `\n\n${PYTHON_CODE_TEMPLATE}\n  \n`,
      "python",
      "JavaScript",
      JS_CODE_TEMPLATE,
    ],
    [
      "the JavaScript starter",
      JS_CODE_TEMPLATE,
      "javascript",
      "Python",
      PYTHON_CODE_TEMPLATE,
    ],
  ])(
    "swaps %s for the other starter without asking",
    (_, code, codeLanguage, option, starter) => {
      const { setCode, setCodeLanguage } = renderEditor(code, codeLanguage);

      chooseLanguage(option);

      expect(setCodeLanguage).toHaveBeenCalledWith(option.toLowerCase());
      expect(setCode).toHaveBeenCalledTimes(1);
      expect(setCode).toHaveBeenCalledWith(starter);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    },
  );

  it("asks before replacing an eval that starts with 'def evaluate(', and Keep my code leaves it", async () => {
    const { setCode, setCodeLanguage } = renderEditor(DEF_EVAL);

    chooseLanguage("JavaScript");

    expect(setCode).not.toHaveBeenCalled();
    expect(setCodeLanguage).toHaveBeenCalledWith("javascript");
    const dialog = await findReplaceDialog("JavaScript");
    expect(within(dialog).getByText(REPLACE_BODY)).toBeInTheDocument();

    choose(dialog, "Keep my code");

    await waitForDialogToClose();
    expect(setCode).not.toHaveBeenCalled();
    expect(setCodeLanguage).toHaveBeenCalledTimes(1);
  });

  it("puts in the JavaScript starter when the user chooses Replace", async () => {
    const { setCode, setCodeLanguage } = renderEditor(DEF_EVAL);

    chooseLanguage("JavaScript");

    expect(setCode).not.toHaveBeenCalled();
    const dialog = await findReplaceDialog("JavaScript");

    choose(dialog, "Replace");

    expect(setCode).toHaveBeenCalledTimes(1);
    expect(setCode).toHaveBeenCalledWith(JS_CODE_TEMPLATE);
    expect(setCodeLanguage).toHaveBeenCalledWith("javascript");
    await waitForDialogToClose();
  });

  it.each([
    ["from typing import Any", TYPING_EVAL, "python", "JavaScript"],
    ["function evaluate(", FUNCTION_EVAL, "javascript", "Python"],
    ["// Return:", RETURN_EVAL, "javascript", "Python"],
  ])(
    "asks before replacing an eval that starts with '%s'",
    async (_, code, codeLanguage, option) => {
      const { setCode, setCodeLanguage } = renderEditor(code, codeLanguage);

      chooseLanguage(option);

      expect(setCode).not.toHaveBeenCalled();
      expect(setCodeLanguage).toHaveBeenCalledWith(option.toLowerCase());
      const dialog = await findReplaceDialog(option);
      expect(within(dialog).getByText(REPLACE_BODY)).toBeInTheDocument();
      expect(setCode).not.toHaveBeenCalled();
    },
  );

  it("keeps the code when the question is closed with Escape", async () => {
    const { setCode, setCodeLanguage } = renderEditor(DEF_EVAL);

    chooseLanguage("JavaScript");

    expect(setCode).not.toHaveBeenCalled();
    const dialog = await findReplaceDialog("JavaScript");

    fireEvent.keyDown(dialog, { key: "Escape" });

    await waitForDialogToClose();
    expect(setCode).not.toHaveBeenCalled();
    expect(setCodeLanguage).toHaveBeenCalledWith("javascript");
  });

  it("focuses Keep my code once the question opens", async () => {
    const { setCode } = renderEditor(DEF_EVAL);

    chooseLanguage("JavaScript");

    const dialog = await findReplaceDialog("JavaScript");
    const keep = within(dialog).getByRole("button", { name: "Keep my code" });
    await waitFor(() => expect(document.activeElement).toBe(keep));
    expect(setCode).not.toHaveBeenCalled();
  });

  it("keeps the code when Enter is pressed on Keep my code", async () => {
    const user = userEvent.setup();
    const { setCode, setCodeLanguage } = renderEditor(DEF_EVAL);

    chooseLanguage("JavaScript");

    const dialog = await findReplaceDialog("JavaScript");
    const keep = within(dialog).getByRole("button", { name: "Keep my code" });
    await waitFor(() => expect(document.activeElement).toBe(keep));

    await user.keyboard("{Enter}");

    await waitForDialogToClose();
    expect(setCode).not.toHaveBeenCalled();
    expect(setCodeLanguage).toHaveBeenCalledWith("javascript");
  });

  it("keeps the code when Enter is pressed on the question itself", async () => {
    const { setCode } = renderEditor(DEF_EVAL);

    chooseLanguage("JavaScript");

    const dialog = await findReplaceDialog("JavaScript");

    fireEvent.keyDown(dialog, { key: "Enter" });

    expect(setCode).not.toHaveBeenCalled();
    expect(dialog).toBeInTheDocument();
  });

  it("keeps the code when the user clicks outside the question", async () => {
    const { setCode, setCodeLanguage } = renderEditor(DEF_EVAL);

    chooseLanguage("JavaScript");

    const dialog = await findReplaceDialog("JavaScript");
    const outside = dialog.closest(".MuiDialog-container");

    fireEvent.mouseDown(outside);
    fireEvent.click(outside);

    await waitForDialogToClose();
    expect(setCode).not.toHaveBeenCalled();
    expect(setCodeLanguage).toHaveBeenCalledWith("javascript");
  });

  it("never replaces the code or asks in a read-only editor", () => {
    const { setCode, setCodeLanguage } = renderEditor(DEF_EVAL, "python", true);

    fireEvent.mouseDown(screen.getByRole("combobox"));

    expect(screen.queryByRole("option")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(setCode).not.toHaveBeenCalled();
    expect(setCodeLanguage).not.toHaveBeenCalled();
  });
});
