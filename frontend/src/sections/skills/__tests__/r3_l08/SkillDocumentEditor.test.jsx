import { useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { parse } from "yaml";

import SkillDocumentEditor from "../../editor/SkillDocumentEditor";

const source = vi.hoisted(() => ({
  mounted: vi.fn(),
  unmounted: vi.fn(),
  props: null,
}));
vi.mock("../../editor/SkillFileEditor", () => {
  function SourceEditor(props) {
    source.props = props;
    useEffect(() => {
      source.mounted();
      return () => source.unmounted();
    }, []);
    return (
      <textarea
        aria-label="Full source"
        value={props.file.content}
        readOnly={props.readOnly}
        onChange={(event) => props.onChange(event.target.value)}
      />
    );
  }
  SourceEditor.propTypes = {
    file: PropTypes.shape({ content: PropTypes.string }).isRequired,
    readOnly: PropTypes.bool,
    onChange: PropTypes.func.isRequired,
  };
  return { default: SourceEditor };
});

const initial =
  "---\r\n# Keep this comment\r\nname: refund-policy\r\ndescription: 'Use when a customer asks about a refund.'  # routing\r\nmetadata:\r\n  owner: équipe\r\n  revision: '001'\r\ncustom-key: [one, two]\r\n---\r\n# Refund policy\r\n\r\nUse the approved 30-day policy.\r\nKeep this exact ending.\n";
const changed = vi.fn();
function Harness({ content = initial, ...props }) {
  const [value, setValue] = useState(content);
  return (
    <>
      <SkillDocumentEditor
        file={{ path: "SKILL.md", content: value }}
        onChange={(next) => {
          changed(next);
          setValue(next);
        }}
        {...props}
      />
      <output data-testid="canonical">{value}</output>
    </>
  );
}
Harness.propTypes = { content: PropTypes.string };
const canonical = () => screen.getByTestId("canonical").textContent;
const description = () =>
  screen.getByRole("textbox", {
    name: "When should the agent use this skill?",
  });
const instructions = () =>
  screen.getByRole("textbox", { name: "Instructions" });
const mode = (name) => fireEvent.click(screen.getByRole("tab", { name }));
const metadata = (content) =>
  parse(content.match(/^---\r?\n([\s\S]*?)\r?\n---/)[1]);

beforeEach(() => {
  vi.clearAllMocks();
  source.props = null;
});
afterEach(cleanup);

describe("one canonical skill document", () => {
  it("preserves comments, unknown keys, quoting, Unicode and mixed newlines through every view", () => {
    render(<Harness />);
    expect(description()).toHaveValue(
      "Use when a customer asks about a refund.",
    );
    expect(screen.getByText("Skill identity · read-only")).toBeVisible();
    expect(
      screen.queryByRole("textbox", { name: /name/i }),
    ).not.toBeInTheDocument();
    mode("Source");
    expect(source.props.file.content).toBe(initial);
    mode("Preview");
    mode("Document");
    mode("Source");
    expect(canonical()).toBe(initial);
    expect(changed).not.toHaveBeenCalled();
    expect(source.mounted).toHaveBeenCalledTimes(1);
    expect(source.unmounted).not.toHaveBeenCalled();
  });

  it("patches only the description scalar, preserving its inline comment and all other bytes", () => {
    render(<Harness />);
    fireEvent.change(description(), {
      target: { value: "Use for the customer's approved refund policy." },
    });
    expect(canonical()).toBe(
      initial.replace(
        "'Use when a customer asks about a refund.'",
        "'Use for the customer''s approved refund policy.'",
      ),
    );
    expect(metadata(canonical()).description).toBe(
      "Use for the customer's approved refund policy.",
    );
    expect(changed).toHaveBeenCalledTimes(1);
  });

  it.each([
    "true",
    "null",
    "100",
    "# comment: [value]",
    'Quoted "text" and \\ path',
    "Line one\nLine two",
    "One\u0085Two Three Four",
  ])("quotes the edited scalar safely: %j", (value) => {
    const content =
      "---\nname: policy\ndescription: original text # retain\nunknown: yes\n---\nBody";
    render(<Harness content={content} />);
    fireEvent.change(description(), { target: { value } });
    expect(metadata(canonical()).description).toBe(value);
    expect(canonical()).toContain(" # retain\nunknown: yes\n---\nBody");
  });

  it("keeps CRLF and untouched mixed-ending lines when a policy clause is edited", () => {
    render(<Harness />);
    fireEvent.change(instructions(), {
      target: { value: instructions().value.replace("30-day", "14-day") },
    });
    expect(canonical()).toBe(initial.replace("30-day", "14-day"));
    fireEvent.change(instructions(), {
      target: { value: instructions().value.replace("14-day", "30-day") },
    });
    expect(canonical()).toBe(initial);
  });

  it("uses the existing body newline for inserted lines without normalizing its untouched suffix", () => {
    render(<Harness />);
    fireEvent.change(instructions(), {
      target: {
        value: instructions().value.replace("Use the", "New clause.\nUse the"),
      },
    });
    expect(canonical()).toBe(
      initial.replace("Use the", "New clause.\r\nUse the"),
    );
  });

  it("does not save no-op textarea normalization or unchanged scalar values", () => {
    render(<Harness />);
    fireEvent.change(instructions(), {
      target: { value: instructions().value },
    });
    fireEvent.change(description(), { target: { value: description().value } });
    mode("Source");
    source.props.onChange(initial);
    expect(changed).not.toHaveBeenCalled();
    expect(canonical()).toBe(initial);
  });

  it("edits an empty body after an EOF delimiter without corrupting the delimiter", () => {
    render(<Harness content={"---\nname: policy\ndescription: policy\n---"} />);
    fireEvent.change(instructions(), { target: { value: "New instructions" } });
    expect(canonical()).toBe(
      "---\nname: policy\ndescription: policy\n---\nNew instructions",
    );
  });

  it("retains body trailing whitespace, blank lines and a YAML end marker", () => {
    const content =
      '---\nname: policy\ndescription: "Use for café 東京"\n...\n\nClause.  \n\n';
    render(<Harness content={content} />);
    fireEvent.change(description(), { target: { value: "Use for returns" } });
    expect(canonical()).toBe(
      content.replace('"Use for café 東京"', '"Use for returns"'),
    );
  });

  it("updates every view from the parent after reload or scope remount, with no private draft copy", () => {
    const { rerender } = render(
      <SkillDocumentEditor
        file={{ path: "SKILL.md", content: initial }}
        onChange={changed}
      />,
    );
    mode("Source");
    mode("Document");
    const other =
      "---\nname: other-scope\ndescription: Other instructions\n---\nNew private body";
    rerender(
      <SkillDocumentEditor
        file={{ path: "SKILL.md", content: other }}
        onChange={changed}
      />,
    );
    expect(description()).toHaveValue("Other instructions");
    expect(instructions()).toHaveValue("New private body");
    expect(source.props.file.content).toBe(other);
    expect(screen.queryByText("refund-policy")).not.toBeInTheDocument();
    expect(changed).not.toHaveBeenCalled();
  });

  it("keeps all document and source mutations read-only while review locks the session", () => {
    render(<Harness readOnly />);
    expect(description()).toHaveAttribute("readonly");
    expect(instructions()).toHaveAttribute("readonly");
    fireEvent.change(description(), { target: { value: "Blocked" } });
    fireEvent.change(instructions(), { target: { value: "Blocked" } });
    mode("Source");
    source.props.onChange("Blocked");
    expect(source.props.readOnly).toBe(true);
    expect(canonical()).toBe(initial);
    expect(changed).not.toHaveBeenCalled();
  });

  it("focuses the complete Source view for check line navigation without resetting its model", () => {
    const { rerender } = render(
      <SkillDocumentEditor
        file={{ path: "SKILL.md", content: initial }}
        onChange={changed}
      />,
    );
    rerender(
      <SkillDocumentEditor
        file={{ path: "SKILL.md", content: initial }}
        target={{ line: 9 }}
        onChange={changed}
      />,
    );
    expect(screen.getByRole("tab", { name: "Source" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(source.props.target).toEqual({ line: 9 });
    mode("Document");
    mode("Source");
    expect(source.mounted).toHaveBeenCalledTimes(1);
  });

  it("uses shared accessible tabs with keyboard focus and panel relationships", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const documentTab = screen.getByRole("tab", { name: "Document" });
    act(() => documentTab.focus());
    await user.keyboard("{ArrowRight}{Enter}");
    const sourceTab = screen.getByRole("tab", { name: "Source" });
    expect(sourceTab).toHaveFocus();
    expect(sourceTab).toHaveAttribute("aria-selected", "true");
    const visible = screen.getByRole("tabpanel");
    expect(visible).toHaveAttribute(
      "id",
      sourceTab.getAttribute("aria-controls"),
    );
    expect(visible).toHaveAttribute("aria-labelledby", sourceTab.id);
    expect(changed).not.toHaveBeenCalled();
  });

  it("shows sample guidance only when requested and discloses allowed-tools limitations", () => {
    render(
      <Harness
        sampleContent
        content={initial.replace(
          "custom-key: [one, two]",
          "allowed-tools: Read",
        )}
      />,
    );
    expect(screen.getByText(/Replace sample policies/)).toBeVisible();
    fireEvent.click(screen.getByText("Advanced metadata · edit in Source"));
    expect(screen.getByText(/does not sandbox this skill/)).toBeInTheDocument();
  });

  it("uses the existing safe preview and snapshot-relative navigation without writing", () => {
    const onOpenPath = vi.fn();
    render(
      <Harness
        onOpenPath={onOpenPath}
        content={`${initial}\n[Policy](references/policy.md)\n<script>window.bad=1</script>\n![Remote](https://example.invalid/image.png)`}
      />,
    );
    mode("Preview");
    fireEvent.click(screen.getByRole("button", { name: "Policy" }));
    expect(onOpenPath).toHaveBeenCalledWith("references/policy.md");
    expect(document.querySelector("script")).toBeNull();
    expect(document.querySelector("img")).toBeNull();
    expect(changed).not.toHaveBeenCalled();
  });

  it("allows temporary empty descriptions without leaving Document or discarding edits", () => {
    render(<Harness />);
    fireEvent.change(description(), { target: { value: "" } });
    expect(description()).toHaveValue("");
    expect(description()).toHaveAttribute("aria-invalid", "true");
    expect(instructions()).toBeVisible();
    expect(canonical()).toBe(
      initial.replace("'Use when a customer asks about a refund.'", "''"),
    );
  });
});

describe("non-destructive Source fallback", () => {
  it.each([
    "# Missing frontmatter",
    "---\nname: policy\ndescription: [not, a, string]\n---\nBody",
    "---\nname: policy\ndescription: first\ndescription: second\n---\nBody",
    "---\nname: policy\ndescription: |\n  Multiline\n  description\n---\nBody",
    '---\nname: policy\ndescription: "Multiple\n  lines"\n---\nBody',
    "---\nname: policy\ndescription: !custom description\n---\nBody",
    "---\nname: policy\ndescription: &shared description\nother: *shared\n---\nBody",
    "---\nname: policy\ndescription: text\nother: &recursive [*recursive]\n---\nBody",
    "---\nname: policy\ndescription: [unterminated\n---\nBody",
    "---\ndescription: text\n---\nBody",
    `---\nname: policy\ndescription: text\n# ${"界".repeat(6000)}\n---\nBody`,
  ])("preserves unsupported YAML unchanged (%#)", (content) => {
    render(<Harness content={content} />);
    expect(screen.getByRole("tab", { name: "Source" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(source.props.file.content).toBe(content);
    expect(screen.getByText(/frontmatter needs Source editing/)).toBeVisible();
    mode("Document");
    expect(
      screen.queryByRole("textbox", { name: "Instructions" }),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Edit original source" }),
    );
    expect(canonical()).toBe(content);
    expect(changed).not.toHaveBeenCalled();
  });

  it("allows explicit source repair and then projects the repaired canonical file", () => {
    render(<Harness content="broken YAML" />);
    fireEvent.change(screen.getByRole("textbox", { name: "Full source" }), {
      target: { value: initial },
    });
    mode("Document");
    expect(description()).toHaveValue(
      "Use when a customer asks about a refund.",
    );
    expect(instructions()).toHaveValue(
      "# Refund policy\n\nUse the approved 30-day policy.\nKeep this exact ending.\n",
    );
  });

  it("never interprets supporting files or AGENTS.md as a skill frontmatter document", () => {
    const content = "---\nliteral: Markdown\n---\nSupporting instructions";
    const { rerender } = render(
      <SkillDocumentEditor
        file={{ path: "references/policy.md", content }}
        onChange={changed}
      />,
    );
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(source.props.file.content).toBe(content);
    rerender(
      <SkillDocumentEditor
        file={{ path: "AGENTS.md", content }}
        readOnly
        onChange={changed}
      />,
    );
    source.props.onChange("blocked");
    expect(changed).not.toHaveBeenCalled();
    expect(source.props.file.content).toBe(content);
  });
});
