import PropTypes from "prop-types";
import { Box, Button, ButtonBase, Stack, Typography } from "@mui/material";
import { useSkillTemplates } from "src/api/skills";
import EmptyState from "src/components/empty-state";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";

export default function LibraryFirstRun({ onCreate, onImport }) {
  const templates = useSkillTemplates();
  const gallery = (templates.data || []).filter((template) => template.id !== "blank");
  return (
    <Stack spacing={2}>
      <EmptyState icon="solar:book-bookmark-linear" title="Create your first skill"
        description="Keep instructions and supporting files together. Start from a template, import existing skills, or write your own."
        sx={{ pt: 4, pb: 2 }}
        action={<Button size="small" variant="contained" onClick={() => onCreate(gallery[0]?.id || "blank")}>Start from a template</Button>}
        secondaryAction={<Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
          <Button size="small" variant="outlined" onClick={onImport}>Import a folder or ZIP</Button>
          <Button size="small" variant="text" onClick={() => onCreate("blank")}>Write from scratch</Button>
        </Stack>} />
      {templates.isPending && <ContentSkeleton variant="rows" rows={2} label="Loading skill templates" />}
      {templates.isError && <ErrorState title="Couldn't load templates" detail="You can still write a skill from scratch or import existing files." onRetry={templates.refetch} />}
      {gallery.length > 0 && <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "repeat(2, minmax(0, 1fr))", lg: "repeat(3, minmax(0, 1fr))" }, gap: 1.5 }}>
        {gallery.map((template) => <ButtonBase key={template.id} onClick={() => onCreate(template.id)}
          sx={{ p: 1.5, border: 1, borderColor: "divider", borderRadius: 1, textAlign: "left", justifyContent: "flex-start", alignItems: "flex-start", "&:hover": { bgcolor: "action.hover" }, "&.Mui-focusVisible": { outline: "2px solid", outlineColor: "primary.main" } }}>
          <Stack spacing={0.5}>
            <Typography variant="m3" component="h3">{template.title}</Typography>
            <Typography variant="s3" color="text.secondary">{template.description}</Typography>
          </Stack>
        </ButtonBase>)}
      </Box>}
    </Stack>
  );
}

LibraryFirstRun.propTypes = { onCreate: PropTypes.func.isRequired, onImport: PropTypes.func.isRequired };
