import { Helmet } from "react-helmet-async";
import CapabilityGate from "src/components/capability-gate";
import ConnectorSettingsPage from "src/sections/settings/falcon-ai-connectors/ConnectorSettingsPage";

export default function FalconAIConnectors() {
  return (
    <>
      <Helmet>
        <title>Falcon AI Connectors | FutureAGI</title>
      </Helmet>
      <CapabilityGate feature="falcon_ai">
        <ConnectorSettingsPage />
      </CapabilityGate>
    </>
  );
}
