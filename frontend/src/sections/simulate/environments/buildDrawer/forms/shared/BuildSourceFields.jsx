import { useState } from "react";
import PropTypes from "prop-types";
import { Button, Checkbox, Chip, FormControlLabel, Stack } from "@mui/material";
import KnowledgeBasePicker from "../../../workspace/sources/KnowledgeBasePicker";
import BuildFilesField from "./BuildFilesField";

export default function BuildSourceFields({ sources, disabled = false }) {
  const [pickerOpen, setPickerOpen] = useState(false);

  return (
    <Stack spacing={1}>
      <BuildFilesField uploads={sources.uploads} disabled={disabled} />
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
};
