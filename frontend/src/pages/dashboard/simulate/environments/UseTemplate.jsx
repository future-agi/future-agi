import { Helmet } from "react-helmet-async";
import UseTemplate from "src/sections/simulate/environments/UseTemplate";

export default function UseTemplatePage() {
  return (
    <>
      <Helmet>
        <title>Use template | Future AGI</title>
      </Helmet>
      <UseTemplate />
    </>
  );
}
