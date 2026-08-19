import { Helmet } from "react-helmet-async";
import EnvironmentGallery from "src/sections/simulate-v2/environments/EnvironmentGallery";

export default function EnvironmentsPage() {
  return (
    <>
      <Helmet>
        <title>Environments | Future AGI</title>
      </Helmet>
      <EnvironmentGallery />
    </>
  );
}
