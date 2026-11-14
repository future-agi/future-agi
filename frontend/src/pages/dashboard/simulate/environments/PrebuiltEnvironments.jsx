import { Helmet } from "react-helmet-async";
import PrebuiltEnvironmentsBrowse from "src/sections/simulate/environments/PrebuiltEnvironmentsBrowse";

export default function PrebuiltEnvironmentsPage() {
  return (
    <>
      <Helmet>
        <title>Prebuilt Environments | Future AGI</title>
      </Helmet>
      <PrebuiltEnvironmentsBrowse />
    </>
  );
}
