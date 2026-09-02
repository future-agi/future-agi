import { Helmet } from "react-helmet-async";
import CompareRuns from "src/sections/simulate-v2/run/CompareRuns";

export default function RunComparePage() {
  return (
    <>
      <Helmet>
        <title>Compare runs | Future AGI</title>
      </Helmet>
      <CompareRuns />
    </>
  );
}
