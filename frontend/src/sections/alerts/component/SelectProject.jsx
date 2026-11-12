import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import { FormSearchSelectFieldState } from "src/components/FromSearchSelectField";
import SvgColor from "src/components/svg-color";
import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";
import { useProjectList } from "src/sections/projects/LLMTracing/common";

export default function SelectProject({
  open,
  value,
  onChange,
  onClose,
  onAction,
}) {
  const theme = useTheme();
  const { data: projectList, isLoading: isLoadingProjects } = useProjectList();

  const projectOptions = useMemo(
    () =>
      projectList?.map(({ id, name }) => ({
        label: name,
        value: id,
      })) || [],
    [projectList],
  );
  // Only a list that loaded and came back empty means there is nothing to
  // pick; while loading or after an error the picker stays.
  const noObserveProjects =
    !isLoadingProjects &&
    Array.isArray(projectList) &&
    projectList.length === 0;
  return (
    <Dialog
      PaperProps={{
        sx: {
          p: 2,
          maxWidth: "560px",
          minWidth: "560px",
        },
      }}
      open={open}
      onClose={() => {
        onClose();
        onChange(null);
      }}
    >
      <DialogTitle
        sx={{
          display: "flex",
          flexDirection: "row",
          justifyContent: "space-between",
          alignItems: "center",
          padding: 0,
          gap: 4,
        }}
      >
        <Typography
          variant="m2"
          color={"text.primary"}
          fontWeight={"fontWeightMedium"}
        >
          Choose a project
        </Typography>
        <IconButton
          onClick={() => {
            onClose();
            onChange(null);
          }}
        >
          <SvgColor
            sx={{
              bgcolor: "text.primary",
            }}
            src="/assets/icons/ic_close.svg"
          />
        </IconButton>
      </DialogTitle>
      <DialogContent
        sx={{
          padding: `${theme.spacing(2.5, 0, 0, 0)} !important`,
        }}
      >
        {noObserveProjects ? (
          <Typography variant="s1" color={"text.secondary"}>
            Alerts watch an Observe project. There are no Observe projects yet:
            send your first trace to create one.
          </Typography>
        ) : (
          <FormSearchSelectFieldState
            fullWidth
            size="small"
            label="Project"
            value={value}
            onChange={(e) => onChange(e?.target?.value)}
            options={projectOptions}
            noOptions={
              isLoadingProjects
                ? "Loading projects…"
                : "Couldn't load projects. Refresh the page to try again."
            }
            inputProps={{ "data-alert-field": "project" }}
          />
        )}
      </DialogContent>
      <DialogActions
        sx={{
          p: 0,
          mt: 4,
        }}
      >
        <Button
          size="small"
          onClick={() => {
            onClose();
            onChange(null);
          }}
          variant="outlined"
        >
          Cancel
        </Button>
        {noObserveProjects ? (
          <Button
            component={RouterLink}
            href={paths.dashboard.observe}
            color={"primary"}
            size="small"
            variant="contained"
            onClick={() => {
              onClose();
              onChange(null);
            }}
          >
            Set up Observe
          </Button>
        ) : (
          <Button
            disabled={!value || isLoadingProjects}
            color={"primary"}
            size="small"
            variant="contained"
            data-alert-project-action="next"
            onClick={() => {
              if (!value) return;
              onAction();
            }}
          >
            Next
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}

SelectProject.propTypes = {
  open: PropTypes.bool,
  onChange: PropTypes.func,
  value: PropTypes.string,
  onClose: PropTypes.func,
  onAction: PropTypes.func,
};
