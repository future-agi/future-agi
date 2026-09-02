import { Helmet } from "react-helmet-async";
import TwinsBrowse from "src/sections/simulate-v2/twins/TwinsBrowse";

export default function TwinsPage() {
  return (
    <>
      <Helmet>
        <title>Twins | Future AGI</title>
      </Helmet>
      <TwinsBrowse />
    </>
  );
}
