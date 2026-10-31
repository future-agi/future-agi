import { Helmet } from "react-helmet-async";
import { SimStoreProvider } from "src/sections/simulate-v2/store";
import SkillsLibrary from "src/sections/simulate-v2/skills/SkillsLibrary";

/** Build → Skills: the workspace skills library, on the simulation store. */
export default function SkillsPage() {
  return (
    <SimStoreProvider>
      <Helmet>
        <title>Skills · Future AGI</title>
      </Helmet>
      <SkillsLibrary />
    </SimStoreProvider>
  );
}
