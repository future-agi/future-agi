import { describe, it, expect, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import HarnessUnreachable from "../HarnessUnreachable";

describe("HarnessUnreachable", () => {
  it("names the URL it tried, so the fix is obvious", () => {
    render(<HarnessUnreachable baseUrl="http://localhost:8777" onRetry={() => {}} />);
    expect(screen.getByText(/http:\/\/localhost:8777/)).toBeInTheDocument();
  });

  it("tells the reader how to start the harness", () => {
    render(<HarnessUnreachable baseUrl="http://localhost:8777" onRetry={() => {}} />);
    expect(screen.getByText(/harness-ui\/server\.py/)).toBeInTheDocument();
  });

  it("retries on request", async () => {
    const onRetry = vi.fn();
    render(<HarnessUnreachable baseUrl="http://localhost:8777" onRetry={onRetry} />);
    await userEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
