import OSSUpgradeGate from "src/components/oss-upgrade-gate";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";
import FalconAIFullPage from "src/sections/falcon-ai/FalconAIFullPage";

export default function FalconAIPage() {
  const { isOSS } = useDeploymentMode();
  if (isOSS) {
    return <OSSUpgradeGate feature="falconAI" />;
  }
  return <FalconAIFullPage />;
}
