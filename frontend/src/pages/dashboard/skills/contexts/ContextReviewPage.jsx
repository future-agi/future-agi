import PropTypes from "prop-types";
import { Helmet } from "react-helmet-async";
import { useParams, useSearchParams } from "react-router-dom";
import { useSkillV11Scope } from "src/api/skills/testing";
import ContextReleaseReview from "src/sections/skills/contexts/ContextReleaseReview";
import { ContextReviewController } from 'src/sections/skills/review/SkillsReviewControllers';

export default function ContextReviewPage({ Controller = ContextReviewController }) {
  const access = useSkillV11Scope();
  const { contextId } = useParams();
  const [params] = useSearchParams();
  const requested = params.get("state");
  const viewState = [
    "loading",
    "error",
    "empty",
    "forbidden",
    "conflict",
    "stale",
    "expired",
    "unavailable",
  ].includes(requested)
    ? requested
    : undefined;
  return (
    <>
      <Helmet>
        <title>Review context · Skills</title>
      </Helmet>
      {access.enabled && Controller ? (
        <Controller
          key={`${access.identity}:${contextId}:${params.get("request") ?? "draft"}`}
          scopeKey={access.identity}
          contextId={contextId}
          requestId={params.get("request")}
          viewState={viewState}
          View={ContextReleaseReview}
        />
      ) : (
        <ContextReleaseReview
          scopeKey={access.enabled ? access.identity : undefined}
          state="unavailable"
        />
      )}
    </>
  );
}
ContextReviewPage.propTypes = { Controller: PropTypes.elementType };
