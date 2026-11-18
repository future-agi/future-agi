import PropTypes from "prop-types";
import { Button } from "@mui/material";
import EmptyState from "src/components/empty-state";
import Iconify from "src/components/iconify";

AnnotationLabelEmpty.propTypes = {
  onCreateClick: PropTypes.func.isRequired,
};

export default function AnnotationLabelEmpty({ onCreateClick }) {
  return (
    <EmptyState
      icon="solar:tag-linear"
      title="No labels yet"
      description="A label is what annotators score: thumbs up or down, a star rating, a number, a category or free text. Queues use them."
      action={
        <Button
          size="small"
          variant="contained"
          color="primary"
          startIcon={<Iconify icon="solar:add-linear" width={16} />}
          onClick={onCreateClick}
        >
          Create label
        </Button>
      }
      docsHref="https://docs.futureagi.com/docs/annotations/concepts/labels"
    />
  );
}
