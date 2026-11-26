import { Button } from "@mui/material";
import { closeSnackbar, enqueueSnackbar } from "notistack";
import { useSearchParams } from "react-router-dom";
import { plural } from "./sources.constants";

export default function useSourceAcceptedToast(onOpen) {
  const [, setParams] = useSearchParams();
  return (review, request) => {
    const decisions = request?.decisions;
    const proposals = review.preview?.proposals || {};
    const total =
      (proposals.field_mappings?.length || 0) +
      (proposals.policy_rules?.length || 0) +
      (proposals.relationships?.length || 0);
    const kept =
      decisions &&
      decisions.record_reviews.reduce((n, r) => n + r.fields.length, 0) +
        decisions.policy_reviews.reduce(
          (n, r) =>
            n + r.rules.filter((rule) => rule.decision === "accepted").length,
          0,
        ) +
        decisions.relationships.length;
    const message =
      total && kept != null && kept > 0
        ? `Added ${kept} of ${plural(total, "change")} to the draft.`
        : `Added ${plural(review.preview?.sources?.length || 0, "source")} to the draft.`;
    enqueueSnackbar(message, {
      variant: "success",
      action: (key) => (
        <Button
          size="small"
          color="inherit"
          onClick={() => {
            closeSnackbar(key);
            onOpen?.();
            setParams(
              (current) => {
                const next = new URLSearchParams(current);
                next.set("reviewChanges", "1");
                return next;
              },
              { replace: true },
            );
          }}
        >
          Review changes
        </Button>
      ),
    });
  };
}
