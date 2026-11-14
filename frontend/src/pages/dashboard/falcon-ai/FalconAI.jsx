import CapabilityGate from "src/components/capability-gate";
import FalconAIFullPage from "src/sections/falcon-ai/FalconAIFullPage";

export default function FalconAIPage() {
  return (
    <CapabilityGate feature="falcon_ai">
      <FalconAIFullPage />
    </CapabilityGate>
  );
}
