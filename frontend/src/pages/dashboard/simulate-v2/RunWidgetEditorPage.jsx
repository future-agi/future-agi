import { Helmet } from "react-helmet-async";
import SimWidgetEditorPage from "src/sections/simulate-v2/run/widgets/SimWidgetEditorPage";

export default function RunWidgetEditorPage() {
  return (
    <>
      <Helmet>
        <title>Widget | Future AGI</title>
      </Helmet>
      <SimWidgetEditorPage />
    </>
  );
}
