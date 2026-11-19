import { describe, it, expect, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import AnnotationLabelEmpty from "../annotation-label-empty";

// Mock Iconify to avoid @iconify/react dependency in tests
vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

describe("AnnotationLabelEmpty", () => {
  it("renders heading and description", () => {
    render(<AnnotationLabelEmpty onCreateClick={() => {}} />);

    expect(screen.getByText("No labels yet")).toBeInTheDocument();
    expect(
      screen.getByText(/A label is what annotators score/),
    ).toBeInTheDocument();
  });

  it("renders Create Label button", () => {
    render(<AnnotationLabelEmpty onCreateClick={() => {}} />);

    expect(
      screen.getByRole("button", { name: /create label/i }),
    ).toBeInTheDocument();
  });

  it("calls onCreateClick when button is clicked", async () => {
    const user = userEvent.setup();
    const handleClick = vi.fn();

    render(<AnnotationLabelEmpty onCreateClick={handleClick} />);

    await user.click(screen.getByRole("button", { name: /create label/i }));
    expect(handleClick).toHaveBeenCalledOnce();
  });
});
