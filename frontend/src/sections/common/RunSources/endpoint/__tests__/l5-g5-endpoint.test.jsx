// options-truth L5 G5 (w0:ignored-switches): no streaming or audio-out in
// the endpoint form; one workspace-secret picker for headers.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AUDIO_MODES, OFFERED_AUDIO_MODES } from "../endpointModel";

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn().mockResolvedValue({
      data: {
        results: [{ name: "ACME_AGENT_TOKEN" }, { name: "OPENAI_API_KEY" }],
      },
    }),
  },
  endpoints: { secrets: { list: "/model-hub/secrets/" } },
}));

const { default: SecretHeaders } = await import("../SecretHeaders");

afterEach(cleanup);

describe("endpoint form options", () => {
  // W6-SCOPE §8 B1 (owner rule): Audio in stays ABSENT until a run builds a
  // multipart body; it sent a byte-identical JSON body (review M6 ruling).
  it("offers text only: audio in and audio out are absent", () => {
    expect(OFFERED_AUDIO_MODES.map((m) => m.id)).toEqual(["text"]);
    expect(AUDIO_MODES.map((m) => m.id)).toContain("audio_in");
    expect(AUDIO_MODES.map((m) => m.id)).toContain("audio_out");
  });

  it("SecretHeaders picks a workspace secret per header", async () => {
    const onChange = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SecretHeaders
          headers={[{ name: "Authorization", secret: "" }]}
          onChange={onChange}
        />
      </QueryClientProvider>,
    );
    fireEvent.mouseDown(screen.getByRole("combobox"));
    const option = await screen.findByRole("option", {
      name: "ACME_AGENT_TOKEN",
    });
    fireEvent.click(option);
    expect(onChange).toHaveBeenCalledWith([
      { name: "Authorization", secret: "ACME_AGENT_TOKEN" },
    ]);
    expect(
      within(screen.getByTestId("secret-headers")).queryByRole("checkbox"),
    ).toBeNull();
  });
});
