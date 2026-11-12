import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import axios from "src/utils/axios";
import SecretHeaders from "src/sections/common/RunSources/endpoint/SecretHeaders";
import { headerRefsOf } from "src/sections/common/RunSources/endpoint/requestModel";
import { testEndpoint } from "src/sections/common/RunSources/endpoint/testEndpoint";
import { experimentBodyOfValues } from "../experimentBody";
import { hydrateHttpSourceValues, serverExperimentBody, withHttpSourceSettings } from "../serverBody";

vi.mock("src/utils/axios", () => ({ default: { post: vi.fn() } }));
vi.mock("src/components/iconify", () => ({ default: () => <span /> }));
vi.mock("src/sections/common/RunSources/endpoint/useSecretNamesState", () => ({
  useSecretNamesState: () => ["OLD", "NEW"],
}));
/* eslint-disable react/prop-types */
vi.mock("src/sections/common/RunSources/endpoint/SecretSelect", () => ({
  default: ({ value, onChange }) => (
    <select aria-label="Header secret" value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="">None</option><option value="OLD">OLD</option><option value="NEW">NEW</option>
    </select>
  ),
}));
/* eslint-enable react/prop-types */

afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

describe("COR-03 hydrated HTTP header editor", () => {
  it("displays, replaces and clears the one reference used by both Test and create", async () => {
    vi.stubGlobal("crypto", { getRandomValues: (bytes) => bytes.fill(17) });
    const detail = { http_configs: [{ id: "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e51", order: 0,
      http: { url: "https://fixture.example/run", method: "POST", body_template: "{}", output_path: "$.reply",
        headers: [{ name: "Authorization", secret_ref: "OLD" }],
      }, variable_mapping: {},
    }] };
    const initial = hydrateHttpSourceValues({ name: "Duplicate", experimentType: "llm" }, detail);
    let current = initial;
    function Editor() {
      const [values, setValues] = React.useState(initial);
      current = values;
      return <SecretHeaders headers={values.promptConfig[0].http.headers} onChange={(headers) => {
        setValues({ ...values, promptConfig: [{ ...values.promptConfig[0],
          http: { ...values.promptConfig[0].http, headers },
        }] });
      }} />;
    }
    axios.post.mockResolvedValue({ data: { status: true, result: {
      simulated: false, provenance: "upstream", status: 200, latency_ms: 1,
      response: { reply: "ok" }, response_is_json: true, error: null,
    } } });
    render(<Editor />);
    expect(screen.getByLabelText("Header secret").value).toBe("OLD");
    const checkRequest = async (reference) => {
      const card = current.promptConfig[0];
      const wire = serverExperimentBody(withHttpSourceSettings(
        experimentBodyOfValues(current, { datasetId: "8d3c1f7e-2b6a-4c11-9d0e-5f6a7b8c9d01" }), current.promptConfig,
      ));
      expect(wire.blocks).toEqual([]);
      expect(wire.body.prompt_config[0].http.headers).toEqual([{ name: "Authorization", secret: reference }]);
      await testEndpoint({ ...card.http, columns: [], row: {}, mapping: {}, secretNames: ["OLD", "NEW"] });
      const sent = axios.post.mock.calls.at(-1)[1].prepared_request.headers
        .filter((header) => header.name === "Authorization");
      expect(sent).toEqual(headerRefsOf(wire.body.prompt_config[0].http.headers));
      expect(sent).toEqual(reference ? [{ name: "Authorization", secret_ref: reference }] : [{ name: "Authorization", value: "" }]);
    };
    await checkRequest("OLD");
    fireEvent.change(screen.getByLabelText("Header secret"), { target: { value: "NEW" } });
    expect(screen.getByLabelText("Header secret").value).toBe("NEW");
    await checkRequest("NEW");
    fireEvent.change(screen.getByLabelText("Header secret"), { target: { value: "" } });
    await checkRequest("");
    expect(detail.http_configs[0].http.headers).toEqual([{ name: "Authorization", secret_ref: "OLD" }]);
  });
});
