import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import ChipCard from "../components/ChipCard";
import ProviderRow from "../components/ProviderRow";
import ContinueRow from "../components/ContinueRow";
import PlatformLogo from "../components/PlatformLogo";
import Field from "../components/Field";

// Use the real owner: it registers the provider icons before ProviderRow renders
// and avoids a stale fixture glyph (logos:gitlab is now mdi:gitlab).
import { REPO_PROVIDERS as PROVIDER_OPTIONS } from "../repoProviders";

describe("ChipCard", () => {
  it("hides unbuilt chips entirely, including their tooltip and keyboard target", () => {
    const onClick = vi.fn();
    const { container } = render(
      <ChipCard label="GitLab" comingSoon onClick={onClick} />,
    );
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByLabelText("Coming soon")).toBeNull();
    expect(screen.queryByText("GitLab")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
    expect(onClick).not.toHaveBeenCalled();
  });

  it("live: clickable and unlabelled", () => {
    const onClick = vi.fn();
    render(<ChipCard label="GitHub" onClick={onClick} />);
    expect(screen.queryByLabelText("Coming soon")).toBeNull();
    fireEvent.click(screen.getByText("GitHub"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("exposes a keyboard button with pressed state and fires on Enter", () => {
    const onClick = vi.fn();
    render(<ChipCard label="GitHub" on onClick={onClick} />);
    const btn = screen.getByRole("button");
    expect(btn).toHaveAttribute("tabindex", "0");
    expect(btn).toHaveAttribute("aria-pressed", "true");
    fireEvent.keyDown(btn, { key: "Enter" });
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});

describe("ProviderRow", () => {
  it("gates coming-soon providers and forwards the live pick", () => {
    const onChange = vi.fn();
    const { container } = render(
      <ProviderRow
        options={PROVIDER_OPTIONS}
        value="github"
        onChange={onChange}
      />,
    );
    expect(container.querySelector("[data-missing-icon]")).toBeNull();
    expect(screen.queryAllByLabelText("Coming soon")).toHaveLength(0);
    expect(screen.queryByText("GitLab")).toBeNull();
    expect(screen.queryByText("Bitbucket")).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("GitHub"));
    expect(onChange).toHaveBeenCalledWith("github");
  });
});

describe("ContinueRow", () => {
  it("disabled shows the hint and a disabled button", () => {
    const onClick = vi.fn();
    render(<ContinueRow disabled hint="Add a repository" onClick={onClick} />);
    expect(screen.getByText("Add a repository")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Build environment/ }),
    ).toBeDisabled();
  });

  it("disabled explains the hint in a tooltip on the button", async () => {
    render(<ContinueRow disabled hint="Add a repository" onClick={vi.fn()} />);
    fireEvent.mouseOver(screen.getByTestId("continue-row-action"));
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Add a repository",
    );
  });

  it("enabled hides the hint and fires on click", () => {
    const onClick = vi.fn();
    render(<ContinueRow hint="Add a repository" onClick={onClick} />);
    expect(screen.queryByText("Add a repository")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Build environment/ }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});

describe("PlatformLogo", () => {
  it("renders the inlined svg for a bundled logo", () => {
    render(<PlatformLogo id="vapi" name="Vapi" />);
    const el = screen.getByLabelText("Vapi");
    expect(el.querySelector("svg")).not.toBeNull();
  });

  it("renders a monogram for a platform without a bundled logo", () => {
    render(<PlatformLogo id="openai_assistants" name="OpenAI Assistants" />);
    expect(screen.getByText("O")).toBeInTheDocument();
  });
});

describe("Field", () => {
  it("renders the required marker and reports typed text", () => {
    const onChange = vi.fn();
    render(<Field label="Repository" required value="" onChange={onChange} />);
    expect(screen.getByText("*")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "owner/repo" },
    });
    expect(onChange).toHaveBeenCalledWith("owner/repo");
  });
});
