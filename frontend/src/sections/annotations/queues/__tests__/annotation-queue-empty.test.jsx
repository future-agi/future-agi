import { describe, it, expect, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import AnnotationQueueEmpty from "../annotation-queue-empty";

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));

describe("AnnotationQueueEmpty", () => {
  it("renders heading and description", () => {
    render(<AnnotationQueueEmpty onCreateClick={() => {}} />);

    expect(
      screen.getByText("No Active annotation queues yet"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Create your first annotation queue/),
    ).toBeInTheDocument();
  });

  it("renders Create Queue button", () => {
    render(<AnnotationQueueEmpty onCreateClick={() => {}} />);
    expect(
      screen.getByRole("button", { name: /create queue/i }),
    ).toBeInTheDocument();
  });

  it("calls onCreateClick when button is clicked", async () => {
    const user = userEvent.setup();
    const handleClick = vi.fn();
    render(<AnnotationQueueEmpty onCreateClick={handleClick} />);

    await user.click(screen.getByRole("button", { name: /create queue/i }));
    expect(handleClick).toHaveBeenCalledOnce();
  });
});
