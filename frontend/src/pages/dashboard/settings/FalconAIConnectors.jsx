import { Helmet } from "react-helmet-async";
import OSSUpgradeGate from "src/components/oss-upgrade-gate";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";
import ConnectorSettingsPage from "src/sections/settings/falcon-ai-connectors/ConnectorSettingsPage";

export default function FalconAIConnectors() {
  const { isOSS } = useDeploymentMode();

  return (
    <>
      <Helmet>
        <title>Falcon AI Connectors | FutureAGI</title>
      </Helmet>
      {isOSS ? (
        <OSSUpgradeGate feature="falconAI" />
      ) : (
        <ConnectorSettingsPage />
      )}
    </>
  );
}
