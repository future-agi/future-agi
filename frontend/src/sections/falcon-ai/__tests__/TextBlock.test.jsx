import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import TextBlock from "../components/TextBlock";

// Mock Iconify (used by the code-block copy button)
vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

const pushMock = vi.fn();
vi.mock("src/routes/hooks", () => ({
  useRouter: () => ({ push: pushMock }),
}));

beforeEach(() => {
  pushMock.mockClear();
});

describe("TextBlock markdown rendering", () => {
  it("renders GFM tables inside a horizontal scroll wrapper", () => {
    const md = [
      "| Name | ID |",
      "|------|----|",
      "| ds-one | `abc` |",
      "| ds-two | `def` |",
    ].join("\n");
    render(<TextBlock content={md} />);
    const table = screen.getByRole("table");
    expect(table).toBeInTheDocument();
    // Wide tables must scroll, not crush columns: the direct parent is the
    // overflow container added via the components.table renderer.
    expect(table.parentElement).toHaveStyle({ overflowX: "auto" });
  });

  it("internal deep links navigate client-side (no full reload)", () => {
    render(
      <TextBlock content="See [my-dataset](/dashboard/develop/abc-123)" />,
    );
    const link = screen.getByRole("link", { name: "my-dataset" });
    expect(link).toHaveAttribute("href", "/dashboard/develop/abc-123");
    fireEvent.click(link);
    expect(pushMock).toHaveBeenCalledExactlyOnceWith(
      "/dashboard/develop/abc-123",
    );
  });

  it("external links open in a new tab with rel=noopener", () => {
    render(<TextBlock content="Docs: [site](https://example.com/docs)" />);
    const link = screen.getByRole("link", { name: "site" });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    fireEvent.click(link);
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("renders nothing for empty content", () => {
    const { container } = render(<TextBlock content="" />);
    expect(container).toBeEmptyDOMElement();
  });
});
