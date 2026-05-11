import axiosInstance, { endpoints } from "src/utils/axios";
import { useQuery } from "@tanstack/react-query";

export async function fetchConversations({
  limit = 20,
  offset = 0,
  search = "",
} = {}) {
  const params = new URLSearchParams();
  params.set("limit", limit);
  params.set("offset", offset);
  if (search) params.set("search", search);
  const { data } = await axiosInstance.get(
    `${endpoints.falconAI.conversations}?${params.toString()}`,
  );
  return data;
}

export async function createConversation(title, contextPage) {
  const { data } = await axiosInstance.post(endpoints.falconAI.conversations, {
    title,
    context_page: contextPage,
  });
  return data;
}

export async function getConversation(id) {
  const { data } = await axiosInstance.get(endpoints.falconAI.conversation(id));
  return data;
}

export async function renameConversation(id, title) {
  const { data } = await axiosInstance.patch(
    endpoints.falconAI.conversation(id),
    { title },
  );
  return data;
}

export async function deleteConversation(id) {
  await axiosInstance.delete(endpoints.falconAI.conversation(id));
}

export async function submitFeedback(messageId, feedback) {
  await axiosInstance.post(endpoints.falconAI.feedback(messageId), {
    feedback,
  });
}

// ---------- Stream status (reconnection support) ----------

export async function checkStreamStatus(conversationId) {
  const { data } = await axiosInstance.get(
    endpoints.falconAI.conversation(conversationId) + "stream-status/",
  );
  return data;
}

// ---------- Connector CRUD ----------

export async function fetchConnectors() {
  const { data } = await axiosInstance.get(endpoints.falconAI.connectors);
  return data;
}

export async function fetchConnector(id) {
  const { data } = await axiosInstance.get(endpoints.falconAI.connector(id));
  return data;
}

export const falconAIQueryKeys = {
  connector: (id) => ["falcon-ai", "connector", id],
};

export function useConnector(id, options = {}) {
  const { enabled = true, ...queryOptions } = options;

  return useQuery({
    queryKey: falconAIQueryKeys.connector(id),
    queryFn: async () => {
      const data = await fetchConnector(id);
      return data?.result || data;
    },
    enabled: Boolean(id) && enabled,
    ...queryOptions,
  });
}

export async function createConnector(payload) {
  const { data } = await axiosInstance.post(
    endpoints.falconAI.connectors,
    payload,
  );
  return data;
}

export async function updateConnector(id, payload) {
  const { data } = await axiosInstance.patch(
    endpoints.falconAI.connector(id),
    payload,
  );
  return data;
}

export async function deleteConnector(id) {
  await axiosInstance.delete(endpoints.falconAI.connector(id));
}

export async function testConnector(id) {
  const { data } = await axiosInstance.post(
    endpoints.falconAI.connectorTest(id),
  );
  return data;
}

export async function discoverConnectorTools(id) {
  const { data } = await axiosInstance.post(
    endpoints.falconAI.connectorDiscover(id),
  );
  return data;
}

export async function authenticateConnector(id) {
  const { data } = await axiosInstance.post(
    endpoints.falconAI.connectorAuth?.(id) ||
      `${endpoints.falconAI.connector(id)}authenticate/`,
  );
  return data;
}

export async function updateConnectorTools(id, enabledToolNames) {
  const { data } = await axiosInstance.patch(
    endpoints.falconAI.connectorTools(id),
    { enabled_tool_names: enabledToolNames },
  );
  return data;
}

// ---------- Skill CRUD ----------

export async function listSkills() {
  const { data } = await axiosInstance.get(endpoints.falconAI.skills);
  return data;
}

export async function createSkill(payload) {
  const { data } = await axiosInstance.post(endpoints.falconAI.skills, payload);
  return data;
}

export async function updateSkill(id, payload) {
  const { data } = await axiosInstance.patch(
    endpoints.falconAI.skill(id),
    payload,
  );
  return data;
}

export async function getSkill(id) {
  const { data } = await axiosInstance.get(endpoints.falconAI.skill(id));
  return data;
}

export async function deleteSkill(id) {
  await axiosInstance.delete(endpoints.falconAI.skill(id));
}

// ---------- File Upload ----------

export async function uploadFile(file) {
  const formData = new FormData();
  formData.append("file", file);
  const { data } = await axiosInstance.post(
    endpoints.falconAI.fileUpload,
    formData,
    { headers: { "Content-Type": "multipart/form-data" } },
  );
  return data;
}
