import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { Box, Stack, Typography, Button, IconButton } from "@mui/material";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import { Upload } from "src/components/upload";
import { formatFileSize } from "src/utils/utils";
import { scenariosFromScript } from "../../_mock/scenarios";
import { SectionCard } from "../../components/primitives";
import { ThinkingBar } from "../../components/loading";
import { ScenarioRow } from "../ScenariosStep";

/**
 * Upload a script and pull the scenarios out of it.
 *
 * Carried over from the previous create-scenario flow, so the dropzone, copy
 * and accepted types are the ones people already know. What is new is the step
 * after it: the beats we read out of the script are shown for review instead of
 * being committed the moment the file lands.
 */
export default function ScriptUpload({ env, onAdd, selected }) {
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [extracted, setExtracted] = useState(null);
  const [checked, setChecked] = useState({});

  const alreadyIn = useMemo(() => new Set(selected.map((s) => s.id)), [selected]);

  const onDrop = (accepted) => {
    const next = accepted?.[0];
    if (!next) return;
    setFile(next);
    setExtracted(null);
    setChecked({});
    // Reading the script is work we actually do, so it is shown happening.
    setBusy(true);
    setTimeout(() => {
      setExtracted(scenariosFromScript(env, next.name));
      setBusy(false);
    }, 1100);
  };

  const clear = () => {
    setFile(null);
    setExtracted(null);
    setChecked({});
  };

  const chosen = useMemo(
    () => (extracted || []).filter((r) => checked[r.id]),
    [extracted, checked],
  );

  return (
    <SectionCard
      title="Upload a script"
      subtitle="Turn a call script, SOP or runbook into the scenarios it describes"
      action={
        <Button
          variant="contained"
          color="primary"
          size="small"
          disabled={chosen.length === 0}
          onClick={() => onAdd(chosen)}
          sx={{ typography: "s2", fontWeight: 700 }}
        >
          Add {chosen.length || ""} {chosen.length === 1 ? "scenario" : "scenarios"}
        </Button>
      }
    >
      <Box sx={{ p: 2.5 }}>
        {file ? (
          <Stack
            direction="row"
            alignItems="center"
            spacing={1.5}
            sx={{ p: 2, borderRadius: 1.25, border: "1px solid", borderColor: "divider" }}
          >
            <SvgColor
              src="/assets/icons/components/ic_script.svg"
              sx={{ width: 24, height: 24, color: "primary.main", flexShrink: 0 }}
            />
            <Box flex={1} minWidth={0}>
              <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>{file.name}</Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                {formatFileSize(file.size)}
              </Typography>
            </Box>
            <IconButton size="small" onClick={clear}>
              <Iconify icon="solar:close-circle-linear" width={17} sx={{ color: "text.subtitle" }} />
            </IconButton>
          </Stack>
        ) : (
          <Upload
            showDropRejection={false}
            hidePreview
            showIllustration={false}
            heading="Upload Script"
            description="Upload AI agent scripts for testing scenarios (TEXT/PDF)"
            uploadIcon={
              <SvgColor
                src="/assets/icons/components/ic_script.svg"
                sx={{ width: 32, height: 32, color: "primary.main" }}
              />
            }
            actionButton={
              <Button size="small" variant="outlined" color="primary">
                Browse Files
              </Button>
            }
            accept={{ "text/plain": [".txt"], "application/pdf": [".pdf"] }}
            sx={{ py: 3 }}
            onDrop={onDrop}
          />
        )}

        {/* Nothing below until there is something to show — the dropzone above
            already says what to do, so an empty panel repeating it is noise. */}
        {(busy || extracted) && (
        <Box sx={{ mt: 2, border: "1px solid", borderColor: "divider", borderRadius: 1.5, overflow: "hidden" }}>
          {busy ? (
            <Box sx={{ px: 2, py: 1.5 }}>
              <ThinkingBar label={`Reading ${file?.name}`} />
            </Box>
          ) : (
            <>
              <Stack
                direction="row"
                alignItems="center"
                spacing={2}
                sx={{ px: 2, py: 1.25, borderBottom: "1px solid", borderColor: "divider" }}
              >
                <Box flex={1}>
                  <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                    {extracted.length} scenarios found in {file?.name}
                  </Typography>
                  <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                    One per beat in the script · click to include
                  </Typography>
                </Box>
                <Button
                  size="small"
                  onClick={() => setChecked(Object.fromEntries(extracted.map((r) => [r.id, true])))}
                  sx={{ typography: "s3", minWidth: 0, px: 0.75, color: "primary.main" }}
                >
                  Select all
                </Button>
              </Stack>
              <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
                {extracted.map((r, i) => (
                  <ScenarioRow
                    key={r.id}
                    row={r}
                    index={i}
                    selectable
                    checked={!!checked[r.id] || alreadyIn.has(r.id)}
                    onToggle={() =>
                      !alreadyIn.has(r.id) && setChecked((c) => ({ ...c, [r.id]: !c[r.id] }))
                    }
                  />
                ))}
              </Stack>
            </>
          )}
        </Box>
        )}
      </Box>
    </SectionCard>
  );
}

ScriptUpload.propTypes = {
  env: PropTypes.object.isRequired,
  onAdd: PropTypes.func,
  selected: PropTypes.array,
};
