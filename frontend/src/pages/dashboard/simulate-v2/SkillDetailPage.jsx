import { Helmet } from "react-helmet-async";
import { SimStoreProvider } from "src/sections/simulate-v2/store";
import SkillPage from "src/sections/simulate-v2/skills/SkillPage";

/** Build → Skills → one skill, on the simulation store. */
export default function SkillDetailPage() {
  return (
    <SimStoreProvider>
      <Helmet>
        <title>Skill · Future AGI</title>
      </Helmet>
      <SkillPage />
    </SimStoreProvider>
  );
}
