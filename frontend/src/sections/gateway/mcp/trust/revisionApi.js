import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";

const options = (organizationId) => {
  if (!organizationId)
    throw new Error("Select an organization before reviewing revisions.");
  return { timeout: 30000, headers: { "X-Organization-Id": organizationId } };
};
const result = (data) => {
  if (
    data?.status !== true ||
    !data.result ||
    typeof data.result !== "object"
  ) {
    throw new Error(
      "The server did not confirm this request. Refresh and try again.",
    );
  }
  return data.result;
};
export async function listRevisions(
  organizationId,
  signal,
  status = "pending",
) {
  const data = result(
    (
      await axios.get(apiPath("/agentcc/mcp/revisions/"), {
        ...options(organizationId),
        params: { status },
        signal,
      })
    ).data,
  );
  if (
    !Array.isArray(data.revisions) ||
    typeof data.gateway_synced !== "boolean"
  ) {
    throw new Error("The server did not confirm the revision list.");
  }
  return data;
}
export async function readPinConfig(gatewayId, organizationId, signal) {
  return result(
    (
      await axios.get(
        apiPath("/agentcc/gateways/{id}/config/", { id: gatewayId }),
        {
          ...options(organizationId),
          signal,
        },
      )
    ).data,
  );
}
export async function decideRevision(action, row, organizationId) {
  if (!["approve", "reject"].includes(action))
    throw new Error("Unknown review action.");
  const data = result(
    (
      await axios.post(
        action === "approve"
          ? apiPath("/agentcc/mcp/revisions/{revision_id}/approve/", {
              revision_id: row.id,
            })
          : apiPath("/agentcc/mcp/revisions/{revision_id}/reject/", {
              revision_id: row.id,
            }),
        {},
        options(organizationId),
      )
    ).data,
  );
  if (
    data.id !== row.id ||
    data.status !== (action === "approve" ? "approved" : "rejected")
  ) {
    throw new Error(
      "The server did not confirm this decision. Refresh and review again.",
    );
  }
  return data;
}
export async function writePin(gatewayId, organizationId, pinning) {
  return result(
    (
      await axios.post(
        apiPath("/agentcc/gateways/{id}/update-config/", { id: gatewayId }),
        { mcp: { pinning } },
        options(organizationId),
      )
    ).data,
  );
}
