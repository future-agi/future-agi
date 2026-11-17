// EmbeddedSurface with requireDoc (the Console) answers the frame's
// embed.models.request {doc, requestId, model?} with the person's model
// list by default, echoing the request's id, and reads
// nothing until the frame asks; Studio answers none, and getModels={null}
// turns it off.
import React from "react";
import { act, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import EmbeddedSurface from "../EmbeddedSurface";

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: http,
}));

const CHILD = "http://127.0.0.1:4191";
const DOC_A = "AAECAwQFBgcICQoLDA0ODw";
const ATTESTATION = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1In0.c2lnbmF0dXJl";
const TOKEN = "fe1r.c2VudGluZWwtY2Fub25pY2Fs.c2VudGluZWwtbWFj";
const ROWS = [{ model: "gpt-4o", name: "GPT-4o", provider: "openai" }];

const renderSurface = (props = {}) =>
  render(
    <ThemeProvider theme={createTheme()}>
      <MemoryRouter initialEntries={["/dashboard/console"]}>
        <Routes>
          <Route
            path="/dashboard/console/*"
            element={
              <EmbeddedSurface
                src={`${CHILD}/?embed=1`}
                getToken={() => "tok_minted"}
                label="Omega Console"
                noKeyTimeoutMs={0}
                requireGate
                requireDoc
                {...props}
              />
            }
          />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  );

const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
const fromChild = async (frame, data) => {
  await act(async () => {
    window.dispatchEvent(
      new MessageEvent("message", {
        data,
        origin: CHILD,
        source: frame.contentWindow,
      }),
    );
  });
  await settle();
};
const spyOnFrame = (frame) => {
  const posted = [];
  vi.spyOn(frame.contentWindow, "postMessage").mockImplementation(
    (message, targetOrigin) => posted.push({ message, targetOrigin }),
  );
  return posted;
};
const connectAndReady = async (frame) => {
  await fromChild(frame, {
    type: "embed.hello",
    protocol: "omega-embed/1",
    doc: DOC_A,
  });
  await fromChild(frame, { type: "embed.gate", state: "ready", doc: DOC_A });
};
const modelPosts = (posted) =>
  posted.filter(({ message }) => message.type === "embed.models");

describe("EmbeddedSurface model list", () => {
  beforeEach(() => {
    http.get.mockReset();
    http.post.mockReset();
    http.get.mockResolvedValue({
      data: {
        status: true,
        result: { models: ROWS, attestation: ATTESTATION },
      },
    });
    http.post.mockResolvedValue({
      data: {
        status: true,
        result: { run_authorization: TOKEN, run_id: "run-1", exp: 1 },
      },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("reads nothing while the frame does not ask, even once its gate is ready", async () => {
    renderSurface();
    const frame = screen.getByTestId("omega-embed-frame");
    const posted = spyOnFrame(frame);

    await connectAndReady(frame);

    expect(http.get).not.toHaveBeenCalled();
    expect(http.post).not.toHaveBeenCalled();
    expect(modelPosts(posted)).toEqual([]);
  });

  it("answers a request without a model with the person's list and mints nothing", async () => {
    renderSurface();
    const frame = screen.getByTestId("omega-embed-frame");
    const posted = spyOnFrame(frame);
    await connectAndReady(frame);

    await fromChild(frame, {
      type: "embed.models.request",
      doc: DOC_A,
      requestId: "req-1",
    });

    expect(http.get).toHaveBeenCalledWith(
      "/agentcc/console-models/",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(http.post).not.toHaveBeenCalled();
    expect(modelPosts(posted)).toEqual([
      {
        message: {
          type: "embed.models",
          doc: DOC_A,
          requestId: "req-1",
          models: ROWS,
          attestation: ATTESTATION,
        },
        targetOrigin: CHILD,
      },
    ]);
  });

  it("answers a request with a model with an authorization for exactly that model", async () => {
    renderSurface();
    const frame = screen.getByTestId("omega-embed-frame");
    const posted = spyOnFrame(frame);
    await connectAndReady(frame);

    await fromChild(frame, {
      type: "embed.models.request",
      doc: DOC_A,
      requestId: "req-1",
      model: "gpt-4o",
    });

    expect(http.post).toHaveBeenCalledTimes(1);
    expect(http.post).toHaveBeenCalledWith(
      "/agentcc/console-run/authorizations/",
      { models: ["gpt-4o"] },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(modelPosts(posted)).toEqual([
      {
        message: {
          type: "embed.models",
          doc: DOC_A,
          requestId: "req-1",
          models: ROWS,
          attestation: ATTESTATION,
          runAuthorization: TOKEN,
        },
        targetOrigin: CHILD,
      },
    ]);
  });

  it("reads no list when the surface turns it off", async () => {
    renderSurface({ getModels: null });
    const frame = screen.getByTestId("omega-embed-frame");
    const posted = spyOnFrame(frame);
    await connectAndReady(frame);

    await fromChild(frame, {
      type: "embed.models.request",
      doc: DOC_A,
      requestId: "req-1",
      model: "gpt-4o",
    });

    expect(http.get).not.toHaveBeenCalled();
    expect(http.post).not.toHaveBeenCalled();
    expect(modelPosts(posted)).toEqual([]);
  });

  it("reads no list for a surface that names no document (Studio)", async () => {
    renderSurface({ requireDoc: false });
    const frame = screen.getByTestId("omega-embed-frame");
    const posted = spyOnFrame(frame);

    await fromChild(frame, { type: "embed.hello", protocol: "omega-embed/1" });
    await fromChild(frame, { type: "embed.gate", state: "ready" });
    await fromChild(frame, {
      type: "embed.models.request",
      doc: DOC_A,
      requestId: "req-1",
      model: "gpt-4o",
    });

    expect(http.get).not.toHaveBeenCalled();
    expect(modelPosts(posted)).toEqual([]);
  });
});
