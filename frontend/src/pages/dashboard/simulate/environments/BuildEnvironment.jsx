import { Helmet } from "react-helmet-async";
import BuildEnvironment from "src/sections/simulate/environments/buildEnvironment/BuildEnvironment";

export default function BuildEnvironmentPage() {
  return (
    <>
      <Helmet>
        <title>Build environment | Future AGI</title>
      </Helmet>
      <BuildEnvironment />
    </>
  );
}
