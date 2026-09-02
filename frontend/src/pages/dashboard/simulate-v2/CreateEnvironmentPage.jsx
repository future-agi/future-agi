import { Helmet } from "react-helmet-async";
import BuildFromAgent from "src/sections/simulate-v2/environments/BuildFromAgent";

export default function CreateEnvironmentPage() {
  return (
    <>
      <Helmet>
        <title>Build environment | Future AGI</title>
      </Helmet>
      <BuildFromAgent />
    </>
  );
}
