import React from "react";
import { render } from "src/utils/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const devtoolsMock = vi.hoisted(() => vi.fn());

vi.mock("@tanstack/react-query-devtools", () => ({
  ReactQueryDevtools: (props) => {
    devtoolsMock(props);
    return <div data-testid="react-query-devtools" />;
  },
}));

import ObserveQueryDevtools from "../ObserveQueryDevtools";

describe("ObserveQueryDevtools", () => {
  beforeEach(() => {
    devtoolsMock.mockClear();
  });

  it("places the developer control at the top right", () => {
    render(<ObserveQueryDevtools />);

    expect(devtoolsMock).toHaveBeenCalledWith(
      expect.objectContaining({
        initialIsOpen: false,
        buttonPosition: "top-right",
      }),
    );
  });
});
