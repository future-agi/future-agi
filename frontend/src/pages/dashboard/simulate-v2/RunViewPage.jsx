import { Helmet } from "react-helmet-async";
import LiveRunView from "src/sections/simulate-v2/run/LiveRunView";

export default function RunViewPage() {
  return (
    <>
      <Helmet>
        <title>Simulation run | Future AGI</title>
      </Helmet>
      <LiveRunView />
    </>
  );
}
