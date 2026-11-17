import { getGatewayAuthenticationErrorMessage } from "src/utils/errorUtils";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import {
  admissionPayload,
  policyRevision,
} from "../../budgets/admission/admissionPolicy";

function admissionHeaders(organizationId) {
  if (!organizationId)
    throw new Error("Select an organization before reading admission policy.");
  return { "X-Organization-Id": organizationId };
}

function assertAdmissionOrganization(config, organizationId) {
  if (config && String(config.organization) !== String(organizationId)) {
    throw new Error(
      "Admission policy organization mismatch. Reload after selecting your organization.",
    );
  }
}

const LEDGER_STATES = ["ready", "absent", "unavailable", "unknown"];

// The gateway's spend-ledger status on an active read: runtime state, never
// part of the saved config. The saved config's own field comes first; an org
// with no saved config (result null) has it beside the result. Neither, or a
// value outside the contract, is "unknown".
export function admissionCapLedger(data) {
  const result = data?.result;
  const own =
    result && typeof result === "object" ? result.admission_cap_ledger : null;
  return (
    [own, data?.admission_cap_ledger].find((value) =>
      LEDGER_STATES.includes(value),
    ) ?? "unknown"
  );
}

// The ledger status a write refused for the spend ledger reports (409
// admission_cap_ledger_unavailable), else null.
export function ledgerRefusal(error) {
  const body = error?.response?.data || error;
  if (body?.code !== "admission_cap_ledger_unavailable") return null;
  return ["absent", "unavailable"].includes(body.ledger_status)
    ? body.ledger_status
    : "unknown";
}

// Explicit headers bind in-flight reads/writes to their initiating organization,
// even if the global Axios defaults change during an organization switch.
// Resolves to the active config (null when none is saved) and the ledger
// status the same read reports.
export async function readAdmissionPolicyState(organizationId, signal) {
  const { data } = await axios.get(endpoints.gateway.orgConfig.active, {
    headers: admissionHeaders(organizationId),
    signal,
  });
  if (
    data?.status !== true ||
    !("result" in data) ||
    (data.result !== null &&
      (typeof data.result !== "object" || Array.isArray(data.result)))
  )
    throw new Error("Could not read admission policy.");
  assertAdmissionOrganization(data.result, organizationId);
  policyRevision(data.result);
  return { config: data.result, ledger: admissionCapLedger(data) };
}

export async function readAdmissionPolicy(organizationId, signal) {
  return (await readAdmissionPolicyState(organizationId, signal)).config;
}

// The organization's own keys, for choosing which key a binding names. Only
// the stable gateway key id, display name and status are kept: the prefix,
// hash and metadata the list also carries never reach the panel. The server
// scopes the list to the request's organization; the explicit header and the
// per-row organization check stop a late read from another organization.
export async function readAdmissionKeys(organizationId, signal) {
  const { data } = await axios.get(endpoints.gateway.apiKeys, {
    headers: admissionHeaders(organizationId),
    signal,
  });
  if (data?.status !== true || !Array.isArray(data.result))
    throw new Error("Could not read this organization's keys.");
  return data.result.flatMap((row) => {
    const id = row?.gateway_key_id ?? row?.gatewayKeyId;
    if (typeof id !== "string" || !id) return [];
    if (
      row.organization != null &&
      String(row.organization) !== String(organizationId)
    )
      throw new Error(
        "Key list organization mismatch. Reload after selecting your organization.",
      );
    return [
      {
        id,
        name: typeof row.name === "string" ? row.name : "",
        status: typeof row.status === "string" ? row.status : "",
      },
    ];
  });
}

export async function writeAdmissionPolicy(
  organizationId,
  config,
  draft,
  activate,
) {
  const headers = admissionHeaders(organizationId);
  assertAdmissionOrganization(config, organizationId);
  const { data } = await axios.post(
    endpoints.gateway.orgConfig.create,
    admissionPayload(config, draft, activate),
    { headers },
  );
  if (data?.status !== true || !data.result)
    throw new Error(
      "Admission policy save was not confirmed. Reload before retrying.",
    );
  assertAdmissionOrganization(data.result, organizationId);
  return data.result;
}

// The version is stored even when the push to the gateway fails
// (gateway_synced: false), so warn rather than report it as live.
function notifyOrgConfigResult(result, successMessage) {
  if (result?.gateway_synced === false) {
    enqueueSnackbar(
      result.gateway_warning ||
        "Gateway sync failed. A gateway connected to this app retries fetching saved configs during its periodic sync.",
      { variant: "warning" },
    );
    return;
  }
  enqueueSnackbar(successMessage, { variant: "success" });
}

export function useOrgConfig() {
  return useQuery({
    queryKey: ["agentcc-org-config"],
    queryFn: async () => {
      const { data } = await axios.get(endpoints.gateway.orgConfig.active);
      return data.result;
    },
    staleTime: 60000,
    retry: false,
  });
}

export function useOrgConfigHistory() {
  return useQuery({
    queryKey: ["agentcc-org-config-history"],
    queryFn: async () => {
      const { data } = await axios.get(endpoints.gateway.orgConfig.list);
      return data.result || data.results || [];
    },
    staleTime: 60000,
  });
}

export function useCreateOrgConfig() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (configData) => {
      const { data } = await axios.post(
        endpoints.gateway.orgConfig.create,
        configData,
      );
      return data.result;
    },
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["agentcc-org-config"] });
      queryClient.invalidateQueries({
        queryKey: ["agentcc-org-config-history"],
      });
      notifyOrgConfigResult(result, "Config saved and activated");
    },
    onError: (err) => {
      enqueueSnackbar(
        getGatewayAuthenticationErrorMessage(err) ||
          err?.message ||
          "Failed to save config",
        {
          variant: "error",
        },
      );
    },
  });
}

export function useActivateOrgConfig() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (cfgId) => {
      const { data } = await axios.post(
        endpoints.gateway.orgConfig.activate(cfgId),
      );
      return data.result;
    },
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["agentcc-org-config"] });
      queryClient.invalidateQueries({
        queryKey: ["agentcc-org-config-history"],
      });
      notifyOrgConfigResult(result, "Rolled back to selected version");
    },
    onError: (err) => {
      enqueueSnackbar(
        getGatewayAuthenticationErrorMessage(err) ||
          err?.message ||
          "Failed to activate config",
        {
          variant: "error",
        },
      );
    },
  });
}
