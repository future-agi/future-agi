import { useMemo } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useQuery } from "@tanstack/react-query";
import { fetchAllObserveProjects } from "src/api/project/observe-project-list";
import { backButtonSx } from "../styles";

export default function StepProjectMapping({ data, onUpdate, onNext, onBack }) {
  const langfuseProjects = data.langfuseProjects || [];
  const selectedLangfuse = data.selectedLangfuseProject || null;

  const {
    data: projects,
    isLoading: projectsLoading,
    isError: projectsError,
    isFetching: projectsFetching,
    refetch: refetchProjects,
  } = useQuery({
    queryKey: ["project-list-observe"],
    queryFn: ({ signal }) => fetchAllObserveProjects({ signal }),
    retry: false,
  });

  const projectOptions = useMemo(() => {
    const opts = (projects || []).map((p) => ({
      id: p.id,
      label: p.name,
    }));
    return [{ id: "__new__", label: "Create new project" }, ...opts];
  }, [projects]);

  const selectedProject = projectOptions.find(
    (o) => o.id === data.futureAgiProjectId,
  );

  const canContinue =
    selectedLangfuse &&
    data.futureAgiProjectId &&
    (data.futureAgiProjectId !== "__new__" || data.newProjectName?.trim());

  return (
    <Stack spacing={2.5}>
      <Typography sx={{ typography: "s1", color: "text.secondary" }}>
        Map a Langfuse project to a FutureAGI project
      </Typography>

      {/* Langfuse project selector */}
      {langfuseProjects.length === 0 ? (
        <Alert severity="warning">
          No projects found in your Langfuse account. Create a project in
          Langfuse first, then retry.
        </Alert>
      ) : (
        <>
          <Autocomplete
            options={langfuseProjects}
            value={selectedLangfuse}
            onChange={(_, newValue) => {
              onUpdate({
                selectedLangfuseProject: newValue,
                futureAgiProjectId: null,
                newProjectName: newValue ? `${newValue.name}-langfuse` : "",
              });
            }}
            getOptionLabel={(o) => o.name}
            isOptionEqualToValue={(opt, val) => opt.id === val?.id}
            renderInput={(params) => (
              <TextField
                {...params}
                label="Langfuse Project"
                size="small"
                placeholder="Select a Langfuse project to sync"
              />
            )}
          />
          {langfuseProjects.length === 1 && (
            <Alert
              severity="info"
              variant="outlined"
              icon={false}
              sx={{ py: 0.5 }}
            >
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                Only 1 project found. Use organization-level API keys to see all
                Langfuse projects.
              </Typography>
            </Alert>
          )}
        </>
      )}

      {/* FutureAGI project selector */}
      {selectedLangfuse && (
        <>
          {projectsError && (
            <Alert
              severity="error"
              action={
                <Button
                  color="inherit"
                  size="small"
                  disabled={projectsFetching}
                  onClick={() => refetchProjects()}
                >
                  Retry
                </Button>
              }
            >
              Failed to load projects. Please try again.
            </Alert>
          )}

          <Autocomplete
            options={projectOptions}
            value={selectedProject || null}
            onChange={(_, newValue) => {
              onUpdate({
                futureAgiProjectId: newValue?.id || null,
                newProjectName:
                  newValue?.id === "__new__"
                    ? `${selectedLangfuse.name}-langfuse`
                    : "",
              });
            }}
            getOptionLabel={(o) => o.label}
            isOptionEqualToValue={(opt, val) => opt.id === val?.id}
            loading={projectsLoading}
            disabled={projectsError}
            renderInput={(params) => (
              <TextField
                {...params}
                label="FutureAGI Project"
                size="small"
                placeholder={
                  projectsLoading
                    ? "Loading projects..."
                    : "Select or create a project"
                }
              />
            )}
          />

          {data.futureAgiProjectId === "__new__" && (
            <TextField
              label="New Project Name"
              value={data.newProjectName}
              onChange={(e) => onUpdate({ newProjectName: e.target.value })}
              fullWidth
              size="small"
              required
            />
          )}
        </>
      )}

      <Box display="flex" justifyContent="space-between">
        <Button
          size="small"
          variant="outlined"
          onClick={onBack}
          startIcon={<Iconify icon="formkit:left" width={16} height={16} />}
          sx={backButtonSx}
        >
          Back
        </Button>
        <Button
          variant="contained"
          color="primary"
          size="small"
          onClick={onNext}
          disabled={!canContinue}
          sx={{ fontWeight: 500 }}
        >
          Continue
        </Button>
      </Box>
    </Stack>
  );
}

StepProjectMapping.propTypes = {
  data: PropTypes.object.isRequired,
  onUpdate: PropTypes.func.isRequired,
  onNext: PropTypes.func.isRequired,
  onBack: PropTypes.func.isRequired,
};
