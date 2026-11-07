import PropTypes from "prop-types";
import { Box, Button, ButtonBase, Stack, Typography } from "@mui/material";
import EmptyState from "src/components/empty-state";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import { useSkillTemplates } from "src/api/skills";

export default function LibraryEmptyState({ onCreate, onImport }) {
  const templates = useSkillTemplates();
  return (
    <Stack spacing={2}>
      <EmptyState title="Give your agents a shared library" icon="solar:book-bookmark-linear"
        description="Start with a template, bring an existing skill folder, or write your own instructions."
        sx={{ pt: 4, pb: 2 }}
        action={<Button variant="contained" onClick={() => document.getElementById("skill-template-gallery")?.focus()}>Start from a template</Button>}
        secondaryAction={<Button variant="outlined" onClick={onImport}>Import a folder or ZIP</Button>} />
      <Box sx={{ textAlign: "center" }}><Button variant="text" onClick={() => onCreate("blank")}>Write from scratch</Button></Box>
      <Stack id="skill-template-gallery" tabIndex={-1} aria-label="Skill templates" spacing={1}>
        <Typography variant="m3" component="h2">Start from a template</Typography>
        {templates.isPending && <ContentSkeleton variant="rows" rows={3} label="Loading templates" />}
        {templates.isError && <ErrorState title="Couldn't load templates" onRetry={templates.refetch} />}
        <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "repeat(2, minmax(0, 1fr))", lg: "repeat(3, minmax(0, 1fr))" }, gap: 1 }}>
          {(templates.data || []).filter((template) => template.id !== "blank").map((template) => (
            <ButtonBase key={template.id} onClick={() => onCreate(template.id)}
              sx={{ p: 1.5, justifyContent: "flex-start", textAlign: "left", border: 1, borderColor: "divider", borderRadius: 1,
                "&:hover": { bgcolor: "action.hover" }, "&.Mui-focusVisible": { outline: "2px solid", outlineColor: "primary.main" } }}>
              <Stack spacing={0.5}>
                <Typography variant="m3" color="text.primary">{template.title}</Typography>
                <Typography variant="s3" color="text.secondary">{template.description}</Typography>
              </Stack>
            </ButtonBase>
          ))}
        </Box>
      </Stack>
    </Stack>
  );
}

LibraryEmptyState.propTypes = { onCreate: PropTypes.func.isRequired, onImport: PropTypes.func.isRequired };
