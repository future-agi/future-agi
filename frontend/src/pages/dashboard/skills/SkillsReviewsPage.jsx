import PropTypes from "prop-types";
import { Helmet } from "react-helmet-async";
import { useSearchParams } from "react-router-dom";
import { useSkillV11Scope } from "src/api/skills/testing";
import ReleaseInbox from "src/sections/skills/review/ReleaseInbox";
import { ReviewInboxController } from 'src/sections/skills/review/SkillsReviewControllers';

// Production defaults to the scope-fenced API controller. Explicitly passing
// null keeps the unavailable boundary inspectable without manufacturing data.
export default function SkillsReviewsPage({ Controller = ReviewInboxController }) {
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
