// Health Check and Reload Config are Owner or Admin only (useGatewayAdmin,
// GatewayAdminOnly in GatewaySettingsSection). A 401 or 403 from either is a
// refusal, not the gateway failing to answer, and is reported as such.
export function isPermissionError(error) {
  // src/utils/axios flattens responses to { ...data, statusCode }.
  return [401, 403].includes(
    error?.statusCode ?? error?.response?.status ?? error?.status,
  );
}
