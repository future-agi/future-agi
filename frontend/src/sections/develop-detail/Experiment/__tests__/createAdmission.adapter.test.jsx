import {
  act,
  cleanup,
  fireEvent,
  screen,
  waitFor,
} from "@testing-library/react";
import { AxiosError } from "axios";
import { afterEach, expect, it, vi } from "vitest";
import {
  ui,
  mountDrawer,
  assertNoSuccessEffects,
} from "./createAdmissionDrawer.fixture";
import axios, { endpoints } from "src/utils/axios";

// The production instance, request contract guard, response interceptor,
// mutation and drawer run unchanged. Only the HTTP transport is substituted.
const originalAdapter = axios.defaults.adapter;
afterEach(() => {
  cleanup();
  axios.defaults.adapter = originalAdapter;
});

it("recovers through the real Axios interceptor, retries identically, and does not treat a name collision as admission", async () => {
  vi.clearAllMocks();
  ui.realNameCheck = true;
  ui.blockAtValidation = false;
  // experiment_submit.py: durable admission failure is 503 (not 4xx).
  const admission = {
    status: false,
    code: "experiment_admission_unconfirmed",
    detail:
      "Configuration saved, but execution admission is unconfirmed. Retry the same submission; do not create another experiment. experiment_id=0b5b7b9e-1111-4c8f-9a8e-0e2f8d1d2c3a",
  };
  // experiments.py -> GeneralMethods.bad_request(EXPERIMENT_NAME_EXISTS).
  const collisionMessage = "Experiment name must be unique";
  const collision = {
    status: false,
    type: "validation_error",
    code: "invalid",
    detail: collisionMessage,
    message: collisionMessage,
    error: collisionMessage,
    result: collisionMessage,
  };
  let nameTaken = false;
  let collisionMode = false;
  const creates = [];
  const normalized = [];
  axios.defaults.adapter = async (config) => {
    let status;
    let data;
    if (
      config.method === "get" &&
      config.url === endpoints.develop.experiment.validateName
    ) {
      status = 200;
      data = {
        status: true,
        result: nameTaken
          ? { is_valid: false, message: "Experiment name already exists" }
          : { is_valid: true },
      };
    } else if (
      config.method === "post" &&
      config.url === endpoints.develop.experiment.create()
    ) {
      creates.push({ url: config.url, data: config.data });
      nameTaken = true;
      status = collisionMode ? 400 : 503;
      data = collisionMode ? collision : admission;
    } else {
      throw new Error(
        `Unexpected transport request: ${config.method} ${config.url}`,
      );
    }
    const response = {
      status,
      data,
      statusText: String(status),
      config,
      headers: {},
    };
    // Custom Axios adapters settle status themselves, just like xhr/http.
    if (!config.validateStatus(status)) {
      throw new AxiosError(
        "HTTP admission response",
        status === 400 ? "ERR_BAD_REQUEST" : "ERR_BAD_RESPONSE",
        config,
        {},
        response,
      );
    }
    return response;
  };
  // Observe AFTER the real interceptor without changing the rejected value.
  const observer = axios.interceptors.response.use(undefined, (error) => {
    normalized.push(error);
    return Promise.reject(error);
  });
  let mounted;
  try {
    mounted = mountDrawer();
    const refetchName = () =>
      act(async () => {
        await mounted.client.refetchQueries({
          queryKey: ["experiment-validate-name"],
        });
        // React Query batches observer notifications after the request resolves.
        // Render the real hook with its newly fetched cache before asserting or
        // clicking, rather than racing that notification with the retry.
        mounted.rerender(mounted.view());
      });
    await refetchName();
    fireEvent.click(screen.getByRole("button", { name: /Run experiment/ }));
    await waitFor(() => expect(ui.snackbar).toHaveBeenCalled());
    expect(normalized[0]).toEqual({
      ...admission,
      statusCode: 503,
      transportCode: "ERR_BAD_RESPONSE",
      // 69a61655d6 preserves only transport-supplied request-id metadata.
      responseHeaders: { "x-request-id": undefined },
    });
    expect(normalized[0]).not.toHaveProperty("response");
    expect(ui.snackbar).toHaveBeenLastCalledWith(admission.detail, {
      variant: "error",
    });
    expect(creates).toHaveLength(1);
    assertNoSuccessEffects();
    await refetchName();
    expect(
      /** @type {{ data: { result: { is_valid: boolean } } }} */ (
        mounted.client.getQueriesData({
          queryKey: ["experiment-validate-name"],
        })[0][1]
      ).data.result.is_valid,
    ).toBe(false);
    expect(screen.queryByTestId("wizard-run-blocked")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Run experiment/ }));
    await waitFor(() => expect(ui.snackbar).toHaveBeenCalledTimes(2));
    expect(creates).toHaveLength(2);
    expect(creates[1]).toEqual(creates[0]);
    expect(screen.getByTestId("experiment-drawer")).toBeInTheDocument();
    assertNoSuccessEffects();

    // A new drawer with a concurrent 400 name collision must not inherit the
    // admission-only retry exemption. Its name read also travels via Axios.
    mounted.unmount();
    mounted.client.clear();
    collisionMode = true;
    nameTaken = false;
    mounted = mountDrawer();
    await refetchName();
    fireEvent.click(screen.getByRole("button", { name: /Run experiment/ }));
    await waitFor(() => expect(ui.snackbar).toHaveBeenCalledTimes(3));
    expect(normalized[2]).toEqual({
      ...collision,
      statusCode: 400,
      transportCode: "ERR_BAD_REQUEST",
      responseHeaders: { "x-request-id": undefined },
    });
    expect(ui.snackbar).toHaveBeenLastCalledWith(collisionMessage, {
      variant: "error",
    });
    await refetchName();
    await waitFor(() =>
      expect(screen.getByTestId("wizard-run-blocked")).toBeDisabled(),
    );
    fireEvent.submit(screen.getByTestId("wizard-run-blocked").closest("form"));
    await act(async () => {});
    expect(creates).toHaveLength(3);
    assertNoSuccessEffects();
  } finally {
    axios.interceptors.response.eject(observer);
    mounted?.unmount();
    mounted?.client.clear();
  }
});
