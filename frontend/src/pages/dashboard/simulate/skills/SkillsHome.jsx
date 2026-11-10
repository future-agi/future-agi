import { Helmet } from "react-helmet-async";
import SkillsPage from "src/sections/simulate/skills/SkillsPage";

export default function SkillsHomePage() {
  return (
    <>
      <Helmet>
        <title>Skills | Future AGI</title>
      </Helmet>
      <SkillsPage />
    </>
  );
}
