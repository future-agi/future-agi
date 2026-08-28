import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen } from "@testing-library/react";
import PropTypes from "prop-types";
import useIsTruncated from "../hooks/useIsTruncated";

// Widths jsdom cannot produce on its own; every element reports these.
let widths = { scroll: 0, client: 0 };
let observers = [];

class RecordingResizeObserver {
  constructor(callback) {
    this.callback = callback;
    this.observed = [];
    this.disconnected = false;
    observers.push(this);
  }

  observe(node) {
    this.observed.push(node);
  }

  disconnect() {
    this.disconnected = true;
  }
}

// Mirrors CustomTooltip: a bare fragment until the text is clipped, a real
// wrapper element after. The element type at that slot changes, so React
// remounts the measured node underneath it.
function Harness({ text }) {
  const [measureRef, isTruncated] = useIsTruncated(text);
  const child = (
    <span ref={measureRef} data-testid="text">
      {text}
    </span>
  );

  return isTruncated ? <em data-testid="wrapper">{child}</em> : <>{child}</>;
}

Harness.propTypes = {
  text: PropTypes.string.isRequired,
};

const overflow = () => {
  widths = { scroll: 200, client: 100 };
};
const fits = () => {
  widths = { scroll: 100, client: 100 };
};

describe("useIsTruncated", () => {
  beforeEach(() => {
    observers = [];
    fits();
    vi.stubGlobal("ResizeObserver", RecordingResizeObserver);
    ["scrollWidth", "clientWidth"].forEach((prop) => {
      Object.defineProperty(HTMLElement.prototype, prop, {
        configurable: true,
        get() {
          return prop === "scrollWidth" ? widths.scroll : widths.client;
        },
      });
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    ["scrollWidth", "clientWidth"].forEach((prop) => {
      delete HTMLElement.prototype[prop];
    });
  });

  it("reports no truncation while the text fits", () => {
    render(<Harness text="Short" />);
    expect(screen.queryByTestId("wrapper")).toBeNull();
  });

  it("reports truncation when the text is clipped", () => {
    overflow();
    render(<Harness text="A description far too long for one line" />);
    expect(screen.getByTestId("wrapper")).toBeInTheDocument();
  });

  // Regression: revealing the tooltip remounts the measured node. A ref object
  // would leave the observer on the detached original and freeze the result.
  it("observes the replacement node after the wrapper remounts it", () => {
    overflow();
    render(<Harness text="A description far too long for one line" />);

    const live = screen.getByTestId("text");
    const active = observers.filter((o) => !o.disconnected);

    expect(active.some((o) => o.observed.includes(live))).toBe(true);
    expect(document.contains(live)).toBe(true);
  });

  it("disconnects the observer left on the replaced node", () => {
    overflow();
    render(<Harness text="A description far too long for one line" />);

    expect(observers.length).toBeGreaterThan(1);
    expect(observers[0].disconnected).toBe(true);
  });

  // The point of re-observing: resizing the card wider must clear the tooltip.
  it("clears truncation when the element grows back", () => {
    overflow();
    render(<Harness text="A description far too long for one line" />);
    expect(screen.getByTestId("wrapper")).toBeInTheDocument();

    fits();
    act(() => {
      observers.filter((o) => !o.disconnected).forEach((o) => o.callback());
    });

    expect(screen.queryByTestId("wrapper")).toBeNull();
  });
});
