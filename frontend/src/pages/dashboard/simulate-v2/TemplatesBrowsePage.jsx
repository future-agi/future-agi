import { Helmet } from "react-helmet-async";
import TemplatesBrowse from "src/sections/simulate-v2/environments/TemplatesBrowse";

export default function TemplatesBrowsePage() {
  return (
    <>
      <Helmet>
        <title>Templates | Future AGI</title>
      </Helmet>
      <TemplatesBrowse />
    </>
  );
}
