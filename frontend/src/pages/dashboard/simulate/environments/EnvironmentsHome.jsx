import { Helmet } from "react-helmet-async";
import EnvironmentsHome from "src/sections/simulate/environments/EnvironmentsHome";

export default function EnvironmentsHomePage() {
  return (
    <>
      <Helmet>
        <title>Environments | Future AGI</title>
      </Helmet>
      <EnvironmentsHome />
    </>
  );
}
