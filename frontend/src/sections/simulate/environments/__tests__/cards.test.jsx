import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import OptionCard from "../cards/OptionCard";
import TemplateHeroCard from "../cards/TemplateHeroCard";
import WebEnvironmentsHeroCard from "../cards/WebEnvironmentsHeroCard";
import { OPTIONS, OPTION_ID } from "../environmentOptions";

const optionById = (id) => OPTIONS.find((o) => o.id === id);

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

describe("OptionCard", () => {
  it("live option: clickable, unlabelled, renders title, blurb and preview chips", () => {
    const onClick = vi.fn();
    const option = optionById(OPTION_ID.SOURCE);
    render(<OptionCard option={option} onClick={onClick} />);

    expect(screen.queryByLabelText("Coming soon")).toBeNull();
    expect(screen.getByText(option.title)).toBeInTheDocument();
    expect(screen.getByText(option.blurb)).toBeInTheDocument();
    option.preview.forEach((chip) => {
      expect(screen.getByText(chip)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("live option: keyboard-focusable and fires on Enter", () => {
    const onClick = vi.fn();
    render(
      <OptionCard option={optionById(OPTION_ID.SOURCE)} onClick={onClick} />,
    );

    const btn = screen.getByRole("button");
    expect(btn).toHaveAttribute("tabindex", "0");
    fireEvent.keyDown(btn, { key: "Enter" });
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it.each([OPTION_ID.MCP, OPTION_ID.LOCAL])(
    "hides unbuilt option %s with no keyboard target",
    (id) => {
      const onClick = vi.fn();
      const { container } = render(
        <OptionCard option={optionById(id)} onClick={onClick} />,
      );
      expect(container).toBeEmptyDOMElement();
      expect(screen.queryByLabelText("Coming soon")).toBeNull();
      expect(screen.queryByRole("button")).toBeNull();
      expect(onClick).not.toHaveBeenCalled();
    },
  );

  it("selected option: exposes aria-pressed", () => {
    render(
      <OptionCard
        option={optionById(OPTION_ID.SOURCE)}
        selected
        onClick={vi.fn()}
      />,
    );
    expect(screen.getByRole("button")).toHaveAttribute("aria-pressed", "true");
  });
});

describe("TemplateHeroCard", () => {
  it("hides the unbuilt prebuilt hero and all of its teaser copy", () => {
    const onClick = vi.fn();
    render(<TemplateHeroCard onClick={onClick} />);

    expect(screen.queryByText("Prebuilt Environments")).toBeNull();
    expect(screen.queryByText("· Fastest")).toBeNull();
    expect(screen.queryByText("Customer Support Line")).toBeNull();
    expect(screen.queryByText("Coding")).toBeNull();
    expect(screen.queryByText("Browser")).toBeNull();
    expect(screen.queryByText("Airline Rebooking")).toBeNull();
    expect(screen.queryByText("+ 10 more")).toBeNull();
    expect(screen.queryByLabelText("Coming soon")).toBeNull();

    expect(screen.queryByRole("button")).toBeNull();
    expect(onClick).not.toHaveBeenCalled();
  });

  it("has no keyboard target for the unbuilt prebuilt hero", () => {
    const onClick = vi.fn();
    render(<TemplateHeroCard onClick={onClick} />);

    expect(screen.queryByRole("button")).toBeNull();
    expect(onClick).not.toHaveBeenCalled();
  });
});

describe("WebEnvironmentsHeroCard", () => {
  it("hides the unbuilt web hero and all of its teaser copy", () => {
    const onClick = vi.fn();
    render(<WebEnvironmentsHeroCard onClick={onClick} />);

    expect(screen.queryByText("Web Environments")).toBeNull();
    ["Slack", "Notion", "Gmail", "Salesforce", "Linear"].forEach((chip) => {
      expect(screen.queryByText(chip)).toBeNull();
    });
    expect(screen.queryByText("+ 8 more")).toBeNull();
    expect(screen.queryByLabelText("Coming soon")).toBeNull();

    expect(screen.queryByRole("button")).toBeNull();
    expect(onClick).not.toHaveBeenCalled();
  });
});
