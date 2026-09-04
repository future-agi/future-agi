import { ApiClient } from './api-client';

// Mirrors futureagi/tfc/utils/api_serializers.py DeploymentInfoResponseSerializer.
export type DeploymentMode = 'oss' | 'ee' | 'cloud';

interface DeploymentInfoResponse { status: boolean; result: { mode: DeploymentMode } }

// GET /api/deployment-info/ is unauthenticated public config
// (futureagi/tfc/views/deployment.py): mode is "cloud" when CLOUD_DEPLOYMENT
// is set, else "ee" when EE_LICENSE_KEY is non-empty, else "oss".
export async function fetchDeploymentMode(api: ApiClient): Promise<DeploymentMode> {
  const res = await api.get<DeploymentInfoResponse>('/api/deployment-info/');
  return res.result.mode;
}
