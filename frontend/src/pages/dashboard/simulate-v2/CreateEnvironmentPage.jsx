import { Helmet } from "react-helmet-async";
import CreateEnvironmentWizard from "src/sections/simulate-v2/environments/CreateEnvironmentWizard";

export default function CreateEnvironmentPage() {
  return (
    <>
      <Helmet>
        <title>Build environment | Future AGI</title>
      </Helmet>
      <CreateEnvironmentWizard />
    </>
  );
}
