import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import AnnotateItemError from "../annotate/annotate-item-error";

describe("AnnotateItemError", () => {
  it.each([
    [403, "Item access denied", /assigned to another annotator/],
    [404, "Item not found", /deleted or is no longer available/],
  ])("explains HTTP %i persistently and offers queue recovery", async (statusCode, title, explanation) => {
    const onBack = vi.fn();
    const onRetry = vi.fn();
    const user = userEvent.setup();
    render(<AnnotateItemError statusCode={statusCode} onBack={onBack} onRetry={onRetry} />);
    expect(screen.getByRole("heading", { name: title })).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent(explanation);
    expect(screen.queryByRole("button", { name: "Try Again" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Back to Queue" }));
    expect(onBack).toHaveBeenCalledOnce();
    expect(onRetry).not.toHaveBeenCalled();
  });

  it.each([500, undefined])("offers retry for a transient failure (%s)", async (statusCode) => {
    const onRetry = vi.fn();
    const user = userEvent.setup();
    render(<AnnotateItemError statusCode={statusCode} onBack={vi.fn()} onRetry={onRetry} />);
    expect(screen.getByRole("heading", { name: "Unable to load item" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Try Again" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});
