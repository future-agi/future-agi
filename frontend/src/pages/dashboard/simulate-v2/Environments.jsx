import { Helmet } from "react-helmet-async";
import StartEnvironment from "src/sections/simulate-v2/environments/StartEnvironment";

export default function EnvironmentsPage() {
  return (
    <>
      <Helmet>
        <title>Environments | Future AGI</title>
      </Helmet>
      <StartEnvironment entry />
    </>
  );
}
