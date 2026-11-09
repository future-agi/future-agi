// The dataset eval list's version line (the version a run uses, its pass
// rule, a pin) and the newer-version notice are one launch surface,
// dataset.evalVersionNotice. Where a launch build hides it, a row shows
// neither and does not read the template's versions; once the surface is
// shown, the row reads them and names the newest one.
import React, { useState } from "react";
import PropTypes from "prop-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "src/utils/test-utils";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";

vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({ dataset: "ds-1" }),
}));
const http = vi.hoisted(() => ({ put: vi.fn(), get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const real = await importOriginal();
  return {
    ...real,
    default: { ...real.default, put: http.put, get: http.get, post: http.post },
  };
});

import SavedEvalsList from "../SavedEvalsList";
import { endpoints } from "src/utils/axios";

const SURFACE = "dataset.evalVersionNotice";
const VERSIONS_URL = endpoints.develop.eval.getEvalVersions("tpl-held");

// A pinned dataset eval as get_evals_list reports it: it runs v2, the
// template's newest live version is v3.
const held = {
  id: "e-held",
  name: "held-eval",
  eval_type: "llm",
  template_name: "notice-template",
  template_id: "tpl-held",
  output_type: "percentage",
  mapping: { output: "col-output" },
  eval_required_keys: ["output"],
  is_experiment_default: false,
  version: "v2",
  version_number: 2,
  pinned_version_id: "ver-2",
  pinned_version_number: 2,
  latest_version: "v3",
  latest_version_number: 3,
  pass_threshold: 0.6,
  higher_is_better: true,
};

function Session({ evals }) {
  const [client] = useState(
    () => new QueryClient({ defaultOptions: { queries: { retry: false } } }),
  );
  return (
    <QueryClientProvider client={client}>
      <SavedEvalsList evals={evals} allColumns={[]} />
    </QueryClientProvider>
  );
}
Session.propTypes = { evals: PropTypes.array.isRequired };

const versionReads = () =>
  http.get.mock.calls.filter(([url]) => url === VERSIONS_URL);

beforeEach(() => {
  http.put.mockReset().mockResolvedValue({ data: { status: true } });
  http.post.mockReset().mockResolvedValue({ data: { status: true } });
  http.get.mockReset().mockImplementation((url) =>
    Promise.resolve(
      url === VERSIONS_URL
        ? {
            data: {
              result: {
                versions: [
                  { version_number: 4 },
                  { version_number: 3 },
                  { version_number: 2 },
                  { version_number: 1 },
                ],
              },
            },
          }
        : { data: { result: null } },
    ),
  );
});

afterEach(() => setLaunchOverrideForTest(null));

describe("dataset eval list version notice", () => {
  it("a launch build that hides it shows no line and reads no versions", async () => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: false,
      madeReal: [],
    });
    render(<Session evals={[held]} />);

    expect(screen.getByText("held-eval")).toBeInTheDocument();
    // Let any query the row starts on mount run before asserting.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(screen.queryByTestId("saved-eval-definition")).toBeNull();
    expect(screen.queryByTestId("saved-eval-newer-version")).toBeNull();
    expect(versionReads()).toEqual([]);
  });

  it("once shown, says what runs and names the template's newest version", async () => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: false,
      madeReal: [SURFACE],
    });
    render(<Session evals={[held]} />);

    expect(screen.getByTestId("saved-eval-row")).toHaveTextContent("held-eval");
    expect(screen.getByTestId("saved-eval-definition")).toHaveTextContent(
      /^↑ Higher is better · Pass ≥ 0\.60 · v2 · pinned$/,
    );
    // The list said v3; the versions read since then has v4.
    await waitFor(() =>
      expect(screen.getByTestId("saved-eval-newer-version")).toHaveTextContent(
        /^v2 → v4 available$/,
      ),
    );
    expect(versionReads()).toHaveLength(1);
  });

  it("an eval that runs the newest version shows no notice", async () => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: false,
      madeReal: [SURFACE],
    });
    http.get.mockImplementation((url) =>
      Promise.resolve({
        data: {
          result:
            url === VERSIONS_URL
              ? { versions: [{ version_number: 3 }, { version_number: 1 }] }
              : null,
        },
      }),
    );
    const follows = {
      ...held,
      id: "e-follows",
      name: "follows-eval",
      version: "v3",
      version_number: 3,
      pinned_version_id: null,
      pinned_version_number: null,
      pass_threshold: 0.7,
    };
    render(<Session evals={[follows]} />);

    expect(screen.getByTestId("saved-eval-definition")).toHaveTextContent(
      /^↑ Higher is better · Pass ≥ 0\.70 · v3$/,
    );
    await waitFor(() => expect(versionReads()).toHaveLength(1));
    expect(screen.queryByTestId("saved-eval-newer-version")).toBeNull();
  });
});
