import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";

import { render } from "src/utils/test-utils";
import RuntimePreflight from "../RuntimePreflight";

const LIVEKIT_ALIASES = ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"];

function preflight(probe) {
  return {
    ready_to_submit: false,
    credentials: { scanned_files: 0, requirements: [], credential_choices: [], probe },
  };
}

describe("RuntimePreflight", () => {
  it("says LiveKit could not be reached, not that the credentials are wrong", () => {
    render(
      <RuntimePreflight
        status="done"
        result={preflight([
          {
            provider: "livekit",
            label: "LiveKit",
            aliases: LIVEKIT_ALIASES,
            ok: false,
            retryable: true,
            message: "Couldn't reach LiveKit (ConnectTimeout); try again",
          },
        ])}
      />
    );

    expect(screen.getByText("Couldn't finish the credential check, try again")).toBeInTheDocument();
    expect(screen.getByText("LiveKit: not checked")).toBeInTheDocument();
    expect(screen.queryByText("Credentials need attention")).toBeNull();
  });

  it("still says the credentials need attention when a provider rejects them", () => {
    render(
      <RuntimePreflight
        status="done"
        result={preflight([
          {
            provider: "livekit",
            label: "LiveKit",
            aliases: LIVEKIT_ALIASES,
            ok: false,
            message: "LiveKit rejected LIVEKIT_URL + LIVEKIT_API_KEY + LIVEKIT_API_SECRET (HTTP 401)",
          },
          {
            provider: "livekit_target",
            label: "LiveKit agent",
            aliases: LIVEKIT_ALIASES,
            ok: false,
            retryable: true,
            message: "Couldn't reach LiveKit (ConnectionError); try again",
          },
        ])}
      />
    );

    expect(screen.getByText("Credentials need attention")).toBeInTheDocument();
    expect(screen.getByText("LiveKit: failed")).toBeInTheDocument();
    expect(screen.getByText("LiveKit agent: not checked")).toBeInTheDocument();
  });
});
