import React from "react";
import PropTypes from "prop-types";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useForm } from "react-hook-form";
import { PersonalKeyContext } from "src/components/personal-key-snippet-context";
import { FormCodeEditor } from "src/components/form-code-editor";
import InstructionCodeCopy from "src/sections/project/NewProject/InstructionCodeCopy";

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("react-syntax-highlighter", async () => {
  const { default: types } = await import("prop-types");
  const Prism = ({ children }) => <pre>{children}</pre>;
  Prism.propTypes = { children: types.node };
  return { Prism };
});
vi.mock("@monaco-editor/react", async () => {
  const { default: types } = await import("prop-types");
  const Editor = ({ value, onChange }) => {
    // Monaco emits programmatic content changes even in read-only mode.
    React.useEffect(() => {
      onChange?.(value);
    }, [value, onChange]);
    return <textarea aria-label="Code" value={value} readOnly />;
  };
  Editor.propTypes = { value: types.string, onChange: types.func };
  return { Editor };
});

const pair = { apiKey: "fixture-api", secretKey: "fixture-secret" };
let clipboardDescriptor;
let commandDescriptor;
let copied;
beforeEach(() => {
  clipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, "clipboard");
  commandDescriptor = Object.getOwnPropertyDescriptor(document, "execCommand");
  copied = null;
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: undefined,
  });
  Object.defineProperty(document, "execCommand", {
    configurable: true,
    value: () => {
      const area = document.activeElement;
      copied = {
        text: area.value,
        blocked:
          area.classList.contains("ph-no-capture") &&
          area.classList.contains("rr-block"),
        inSurface: Boolean(area.closest('[data-testid="surface"]')),
      };
      return true;
    },
  });
});
afterEach(() => {
  if (clipboardDescriptor)
    Object.defineProperty(navigator, "clipboard", clipboardDescriptor);
  else delete navigator.clipboard;
  if (commandDescriptor)
    Object.defineProperty(document, "execCommand", commandDescriptor);
  else delete document.execCommand;
});
function EditorSurface({ held }) {
  const { control } = useForm({
    defaultValues: { code: 'secret_key="YOUR_SECRET_KEY"' },
  });
  return (
    <div data-testid="surface">
      <PersonalKeyContext.Provider value={{ pair: held }}>
        <FormCodeEditor control={control} fieldName="code" readOnly />
      </PersonalKeyContext.Provider>
    </div>
  );
}

EditorSurface.propTypes = {
  held: PropTypes.shape({
    apiKey: PropTypes.string,
    secretKey: PropTypes.string,
  }),
};

describe("personal snippet clipboard fallback", () => {
  it("copies the filled instruction inside the recording-excluded surface", async () => {
    render(
      <div data-testid="surface">
        <PersonalKeyContext.Provider value={{ pair }}>
          <InstructionCodeCopy
            text='secret_key="YOUR_SECRET_KEY"'
            language="python"
          />
        </PersonalKeyContext.Provider>
      </div>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Copy code" }));
    await waitFor(() =>
      expect(copied).toEqual({
        text: 'secret_key="fixture-secret"',
        blocked: true,
        inSurface: true,
      }),
    );
    expect(document.querySelector('textarea[aria-hidden="true"]')).toBeNull();
  });
  it("copies the filled editor without writing credentials into form state", async () => {
    const view = render(<EditorSurface held={null} />);
    expect(screen.getByLabelText("Code")).toHaveValue(
      'secret_key="YOUR_SECRET_KEY"',
    );
    view.rerender(<EditorSurface held={pair} />);
    expect(screen.getByLabelText("Code")).toHaveValue(
      'secret_key="fixture-secret"',
    );
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() =>
      expect(copied).toEqual({
        text: 'secret_key="fixture-secret"',
        blocked: true,
        inSurface: true,
      }),
    );
    expect(document.querySelector('textarea[aria-hidden="true"]')).toBeNull();
    view.rerender(<EditorSurface held={null} />);
    expect(screen.getByLabelText("Code")).toHaveValue(
      'secret_key="YOUR_SECRET_KEY"',
    );
  });
});
