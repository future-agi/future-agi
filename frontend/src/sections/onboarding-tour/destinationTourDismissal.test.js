import { beforeEach, describe, expect, it } from "vitest";
import {
  destinationTourDismissalKey,
  destinationTourStorageIdentity,
  dismissDestinationTourAnchor,
  isDestinationTourAnchorDismissed,
  resetDestinationTourAnchorDismissal,
} from "./destinationTourDismissal";

describe("destinationTourDismissal", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  it("stores dismissed anchors under the current session user", () => {
    window.sessionStorage.setItem("currentUserId", "usr-1");

    dismissDestinationTourAnchor({ anchor: "prompt_run_test_button" });

    expect(destinationTourStorageIdentity()).toBe("usr-1");
    expect(
      JSON.parse(
        window.localStorage.getItem(destinationTourDismissalKey("usr-1")),
      ),
    ).toEqual(["prompt_run_test_button"]);
    expect(
      isDestinationTourAnchorDismissed({
        anchor: "prompt_run_test_button",
        identity: "usr-1",
      }),
    ).toBe(true);
    expect(
      isDestinationTourAnchorDismissed({
        anchor: "prompt_run_test_button",
        identity: "usr-2",
      }),
    ).toBe(false);
  });

  it("removes a dismissed anchor for replay", () => {
    dismissDestinationTourAnchor({
      anchor: "gateway_request_button",
      identity: "usr-1",
    });

    resetDestinationTourAnchorDismissal({
      anchor: "gateway_request_button",
      identity: "usr-1",
    });

    expect(
      isDestinationTourAnchorDismissed({
        anchor: "gateway_request_button",
        identity: "usr-1",
      }),
    ).toBe(false);
  });
});
