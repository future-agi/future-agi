import { Helmet } from "react-helmet-async";
import TwinDetail from "src/sections/simulate-v2/twins/TwinDetail";

export default function TwinDetailPage() {
  return (
    <>
      <Helmet>
        <title>Twin | Future AGI</title>
      </Helmet>
      <TwinDetail />
    </>
  );
}
