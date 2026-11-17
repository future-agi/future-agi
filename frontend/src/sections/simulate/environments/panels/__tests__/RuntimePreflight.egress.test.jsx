import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import RuntimePreflight from "../RuntimePreflight";

// On every Environment connect path, preflight stopped at the
// sandbox egress guard and the panel showed only its raw sentence, with no
// plain reason and no next step.
const RAW = "private/reserved host 'localhost' is not allowed in egress";

// What the panel receives: HostedHarnessError.as_dict() answers
// {error, message, retryable}, and the axios interceptor (src/utils/axios.js)
// rejects with that body spread flat plus statusCode.
const egressRefusal = {
  error: "egress_domain_private",
  message: RAW,
  retryable: false,
  statusCode: 400,
};

describe("RuntimePreflight when preflight can't run", () => {
  it("explains the egress refusal in plain words with next steps, raw text behind Details", () => {
    const onRun = vi.fn();
    render(
      <RuntimePreflight status="error" onRun={onRun} error={egressRefusal} />,
    );

    expect(screen.getByText("Preflight couldn't run")).toBeInTheDocument();
    expect(
      screen.getByText(
        "This server blocks private and local addresses, so it can't reach localhost.",
      ),
    ).toBeInTheDocument();
    // Both ways forward: the entered address, or this server's own settings.
    expect(
      screen.getByText(
        /use a public repository or host URL, or switch to Code upload/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/so ask whoever runs it to change them/),
    ).toBeInTheDocument();

    // The operator's sentence stays out of sight until asked for.
    expect(screen.queryByText(RAW)).toBeNull();
    const details = screen.getByRole("button", { name: "Details" });
    expect(details).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(details);
    expect(details).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(RAW)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRun).toHaveBeenCalledTimes(1);
  });

  it("keeps Try again whole beside the longer egress explanation", () => {
    render(
      <RuntimePreflight status="error" onRun={vi.fn()} error={egressRefusal} />,
    );

    // The text and Try again share a flex row, and the theme gives every
    // Button minWidth 0 and a one-line label. Unless the button refuses to
    // shrink, the long explanation squeezes it narrower than "Try again" and
    // the label spills out of its outline over the text.
    expect(
      getComputedStyle(screen.getByRole("button", { name: "Try again" }))
        .flexShrink,
    ).toBe("0");
  });

  it("recognises the egress guard by its text when no error code comes with it", () => {
    const raw = "private/reserved host '10.0.0.5' is not allowed in egress";
    render(
      <RuntimePreflight
        status="error"
        onRun={vi.fn()}
        error={{ response: { data: { detail: raw } } }}
      />,
    );

    expect(
      screen.getByText(
        "This server blocks private and local addresses, so it can't reach 10.0.0.5.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(raw)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Details" }));
    expect(screen.getByText(raw)).toBeInTheDocument();
  });

  it("keeps any other failure's text and adds a way forward, with no Details", () => {
    render(
      <RuntimePreflight
        status="error"
        onRun={vi.fn()}
        error={{ response: { data: { detail: "Source archive unavailable" } } }}
      />,
    );

    expect(screen.getByText("Source archive unavailable")).toBeInTheDocument();
    expect(
      screen.getByText("Check what you entered above, then try again."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Details" })).toBeNull();
    expect(screen.queryByText(/blocks private and local addresses/)).toBeNull();
  });
});
