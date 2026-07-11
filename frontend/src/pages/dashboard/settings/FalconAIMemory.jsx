import { Helmet } from "react-helmet-async";
import MemorySettingsPage from "src/sections/settings/falcon-ai-memory/MemorySettingsPage";

export default function FalconAIMemory() {
  return (
    <>
      <Helmet>
        <title>Falcon AI Memory | FutureAGI</title>
      </Helmet>
      <MemorySettingsPage />
    </>
  );
}
