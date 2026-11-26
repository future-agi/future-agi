import { useState } from "react";
import PropTypes from "prop-types";
import {
  Button,
  Checkbox,
  Chip,
  FormControlLabel,
  Stack,
  Typography,
} from "@mui/material";
import KnowledgeBasePicker from "../../../workspace/sources/KnowledgeBasePicker";
import BuildFilesField from "./BuildFilesField";

export default function BuildSourceFields({
  sources,
  disabled = false,
  showUpload = true,
}) {
  const [pickerOpen, setPickerOpen] = useState(false);

  return (
    <Stack spacing={1}>
      {showUpload && (
        <BuildFilesField uploads={sources.uploads} disabled={disabled} />
      )}
      {sources.uploads.done
        .filter(({ name }) => name.toLowerCase().endsWith(".pdf"))
        .map(({ name, documentId }) => (
          <FormControlLabel
            key={documentId}
            disabled={disabled}
            control={
              <Checkbox
                checked={Boolean(sources.policy[documentId])}
                onChange={(event) =>
                  sources.setPolicy((current) => ({
                    ...current,
                    [documentId]: event.target.checked,
                  }))
                }
              />
            }
            label={`Use ${name} as policy`}
          />
        ))}
      {sources.uploads.done.some(({ name }) =>
        name.toLowerCase().endsWith(".pdf"),
      ) && (
        <Typography variant="caption" color="text.secondary">
          Policy mode proposes supported instructions as rules. Other passages
          remain background.
        </Typography>
      )}
      {sources.knowledge.map(({ label, selection }) => (
        <Chip
          key={selection.kb_id}
          label={label}
          onDelete={
            disabled
              ? undefined
              : () => sources.removeKnowledge(selection.kb_id)
          }
        />
      ))}
      <Button
        sx={{ alignSelf: "start" }}
        size="small"
        onClick={() => setPickerOpen(true)}
        disabled={disabled}
      >
        Select Knowledge Base
      </Button>
      {pickerOpen && (
        <KnowledgeBasePicker
          open
          onClose={() => setPickerOpen(false)}
          onPick={(selection) => {
            sources.addKnowledge(selection);
            setPickerOpen(false);
          }}
        />
      )}
    </Stack>
  );
}

BuildSourceFields.propTypes = {
  sources: PropTypes.object.isRequired,
  disabled: PropTypes.bool,
  showUpload: PropTypes.bool,
};
