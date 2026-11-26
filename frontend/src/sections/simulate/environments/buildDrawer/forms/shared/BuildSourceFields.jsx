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
  const pdfs = sources.uploads.done.filter(({ name }) =>
    name.toLowerCase().endsWith(".pdf"),
  );

  return (
    <Stack spacing={1}>
      {showUpload && (
        <BuildFilesField uploads={sources.uploads} disabled={disabled} />
      )}
      {pdfs.length > 0 && (
        <Stack>
          {pdfs.map(({ name, documentId }) => (
            <FormControlLabel
              key={documentId}
              disabled={disabled}
              sx={{
                m: 0,
                gap: 0.5,
                "& .MuiFormControlLabel-label": { typography: "s1" },
              }}
              control={
                <Checkbox
                  size="small"
                  sx={{ p: 0.5 }}
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
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            Policy mode proposes supported instructions as rules. Other passages
            remain background.
          </Typography>
        </Stack>
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
        variant="outlined"
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
