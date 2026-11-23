import { Helmet } from "react-helmet-async";
import DesignPage from "src/sections/simulate/environments/design/DesignPage";

export default function EnvironmentDesignPage() {
  return (
    <>
      <Helmet>
        <title>Review the design | Future AGI</title>
      </Helmet>
      <DesignPage />
    </>
  );
}
