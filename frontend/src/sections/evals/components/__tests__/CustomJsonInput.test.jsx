import { describe, it, expect, vi } from "vitest";
import { useState } from "react";
import PropTypes from "prop-types";
import { render, screen } from "src/utils/test-utils";
import { fireEvent } from "@testing-library/react";
import { CustomJsonInput } from "../TestPlayground";

// Monaco doesn't run in jsdom — swap it for a plain textarea that mirrors the
// value prop and forwards edits, so we can drive handleJsonChange directly.
vi.mock("../CodeEditor", () => ({
  default: ({ value, onChange }) => (
    <textarea
      data-testid="json-editor"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  ),
}));

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));

// Controlled harness so inputValues round-trips through onInputChange like the
// real parent does.
function Harness({ variables }) {
  const [inputValues, setInputValues] = useState({});
  return (
    <CustomJsonInput
      variables={variables}
      inputValues={inputValues}
      onInputChange={(k, v) => setInputValues((p) => ({ ...p, [k]: v }))}
    />
  );
}

Harness.propTypes = {
  variables: PropTypes.arrayOf(PropTypes.string).isRequired,
};

const editor = () => screen.getByTestId("json-editor");

describe("CustomJsonInput — scaffold sync", () => {
  it("does not rewrite the editor on a jsonText-only change that drops a variable key", () => {
    render(<Harness variables={["conversation"]} />);
    // Mount scaffolds the variable in.
    expect(editor().value).toContain("conversation");

    // User deletes the key; JSON stays valid, variable set unchanged.
    fireEvent.change(editor(), { target: { value: "{}" } });

    // The key must NOT be re-injected (the regression this fix removes).
    expect(editor().value).toBe("{}");
  });

  it("back-fills a variable added while the JSON was invalid, once it parses again", () => {
    const { rerender } = render(<Harness variables={["conversation"]} />);

    // Make the JSON invalid mid-edit.
    fireEvent.change(editor(), { target: { value: "{bad" } });
    expect(editor().value).toBe("{bad");

    // A new variable appears while the JSON can't be parsed — deferred.
    rerender(<Harness variables={["conversation", "topic"]} />);
    expect(editor().value).toBe("{bad");

    // JSON becomes valid again → the deferred add replays.
    fireEvent.change(editor(), { target: { value: '{"conversation":""}' } });
    expect(editor().value).toContain("topic");
  });
});
