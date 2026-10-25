import { Helmet } from "react-helmet-async";
import DeveloperPage from "src/sections/settings/developer/DeveloperPage";

export default function Developer() {
  return (
    <>
      <Helmet>
        <title>Developer | FutureAGI</title>
      </Helmet>
      <DeveloperPage />
    </>
  );
}
