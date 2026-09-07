import { Helmet } from "react-helmet-async";
import EnvironmentGallery from "src/sections/simulate-v2/environments/EnvironmentGallery";

/**
 * Browse existing environments and the template catalog.
 *
 * The new /simulate/environments landing is a picker-first entry flow —
 * "how do you want to start?". This page keeps the older gallery view
 * available for people who just want to look through their existing
 * environments or scan the full template catalog.
 */
export default function EnvironmentsBrowsePage() {
  return (
    <>
      <Helmet>
        <title>Browse environments | Future AGI</title>
      </Helmet>
      <EnvironmentGallery />
    </>
  );
}
