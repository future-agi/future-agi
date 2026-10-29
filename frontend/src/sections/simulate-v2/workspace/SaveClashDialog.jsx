import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Stack, Typography } from "@mui/material";
import { ConfirmDialog } from "src/components/custom-dialog";
import Iconify from "src/components/iconify";
import { envChangeLabel, pendingEnvChanges } from "../_mock/versions";

const ago = (iso) => {
  const m = Math.max(
    1,
    Math.round((Date.now() - Date.parse(iso || "")) / 60000),
  );
  return m < 60 ? `${m} min ago` : `${Math.round(m / 60)} h ago`;
};

/**
 * Someone else saved this environment while you were editing (roadmap
 * "Saving, versions and concurrent changes"): both sets of changes side by
 * side, the field you both touched as a choice, and three ways out — keep
 * theirs, save both (theirs, then yours on top), or put yours in a copy.
 * Nothing is overwritten without one of these.
 */
export default function SaveClashDialog({
  open,
  save,
  envState,
  onClose,
  onKeepTheirs,
  onSaveBoth,
  onSaveCopy,
}) {
  const [pick, setPick] = useState("mine");
  if (!save) return null;
  const mine = pendingEnvChanges(envState);
  const first = save.by.split(" ")[0];

  const Row = ({ kind, detail }) => (
    <Stack
      direction="row"
      spacing={1.25}
      sx={{
        px: 1.25,
        py: 0.75,
        "& + &": { borderTop: "1px solid", borderColor: "divider" },
      }}
    >
      <Typography
        sx={{
          typography: "s3",
          color: "text.tertiary",
          width: 104,
          flexShrink: 0,
        }}
      >
        {envChangeLabel(kind)}
      </Typography>
      <Typography sx={{ typography: "s3", flex: 1 }}>{detail}</Typography>
    </Stack>
  );
  Row.propTypes = { kind: PropTypes.string, detail: PropTypes.string };

  const Group = ({ title, children }) => (
    <Box>
      <Typography sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}>
        {title}
      </Typography>
      <Box
        sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}
      >
        {children}
      </Box>
    </Box>
  );
  Group.propTypes = { title: PropTypes.string, children: PropTypes.node };

  const Choice = ({ id, label, detail }) => (
    <Stack
      direction="row"
      spacing={1}
      alignItems="flex-start"
      role="radio"
      aria-checked={pick === id}
      tabIndex={0}
      onClick={() => setPick(id)}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && setPick(id)}
      sx={{
        flex: 1,
        p: 1,
        borderRadius: 1,
        border: "1px solid",
        borderColor: pick === id ? "text.primary" : "divider",
        cursor: "pointer",
      }}
    >
      <Iconify
        icon={pick === id ? "solar:check-circle-bold" : "solar:circle-linear"}
        width={16}
        sx={{ color: "text.secondary", flexShrink: 0, mt: "1px" }}
      />
      <Box>
        <Typography sx={{ typography: "s3", fontWeight: 600 }}>
          {label}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          {detail}
        </Typography>
      </Box>
    </Stack>
  );
  Choice.propTypes = {
    id: PropTypes.string,
    label: PropTypes.string,
    detail: PropTypes.string,
  };

  return (
    <ConfirmDialog
      open={open}
      onClose={onClose}
      PaperProps={{ sx: { maxWidth: 560 } }}
      title={`${save.by} saved this environment while you were editing`}
      content={
        <Stack spacing={1.5}>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            Saving now would make a version on top of theirs. Pick what happens
            to both sets of changes — nothing is overwritten until you do.
          </Typography>
          <Group title={`Saved by ${save.by} · ${ago(save.at)}`}>
            <Row kind="toolbehaviour" detail={save.detail} />
          </Group>
          <Group title={`Your unsaved edits · ${mine.length}`}>
            {mine.map((c, i) => (
              <Row
                key={`${c.kind}-${c.at || i}`}
                kind={c.kind}
                detail={c.detail || envChangeLabel(c.kind)}
              />
            ))}
          </Group>
          {save.overlap && (
            <Box>
              <Typography sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}>
                {`You both changed ${save.tool} — which stays if you save both?`}
              </Typography>
              <Stack direction="row" spacing={1} role="radiogroup">
                <Choice id="mine" label="Yours" detail={save.overlap.detail} />
                <Choice id="theirs" label={`${first}'s`} detail={save.detail} />
              </Stack>
            </Box>
          )}
        </Stack>
      }
      action={
        <>
          <Button
            size="small"
            variant="outlined"
            onClick={onSaveCopy}
            sx={{ color: "text.primary", borderColor: "divider" }}
          >
            Save mine as a copy
          </Button>
          <Button
            size="small"
            variant="outlined"
            onClick={onKeepTheirs}
            sx={{ color: "text.primary", borderColor: "divider" }}
          >
            {`Keep ${first}'s`}
          </Button>
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={() => onSaveBoth(pick)}
          >
            Save both
          </Button>
        </>
      }
    />
  );
}

SaveClashDialog.propTypes = {
  open: PropTypes.bool,
  save: PropTypes.object,
  envState: PropTypes.object,
  onClose: PropTypes.func,
  onKeepTheirs: PropTypes.func,
  onSaveBoth: PropTypes.func,
  onSaveCopy: PropTypes.func,
};
