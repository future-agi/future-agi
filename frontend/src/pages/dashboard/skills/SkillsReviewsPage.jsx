import PropTypes from "prop-types";
import { Helmet } from "react-helmet-async";
import { useSearchParams } from "react-router-dom";
import { useSkillV11Scope } from "src/api/skills/testing";
import ReleaseInbox from "src/sections/skills/review/ReleaseInbox";

// Integration supplies an API-owning controller. Do not invent URLs or a second
// HTTP/query client while the review endpoints are unavailable in F12.
export default function SkillsReviewsPage({ Controller }) {
  const access = useSkillV11Scope();
  const [params] = useSearchParams();
  const requested = params.get("state");
  const viewState = [
    "loading",
    "error",
    "empty",
    "forbidden",
    "unavailable",
  ].includes(requested)
    ? requested
    : undefined;
  return (
    <>
      <Helmet>
        <title>Reviews · Skills</title>
      </Helmet>
      {access.enabled && Controller ? (
        <Controller
          key={access.identity}
          scopeKey={access.identity}
          viewState={viewState}
          View={ReleaseInbox}
        />
      ) : (
        <ReleaseInbox
          scopeKey={access.enabled ? access.identity : undefined}
          state="unavailable"
        />
      )}
    </>
  );
}
SkillsReviewsPage.propTypes = { Controller: PropTypes.elementType };
