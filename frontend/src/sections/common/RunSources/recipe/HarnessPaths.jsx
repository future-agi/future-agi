// How to run a harness on your machine (lane Rb MUST 1; options-truth L1
// G3 B). dataset integration FE-0: the Live connection tab is removed (its command does
// not exist); only `Run on your machine` remains, and the Agent harness
// card that reaches it is absent from the picker until FE-1.
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  ButtonBase,
  Collapse,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { VOCAB } from "src/sections/common/vocab";
import { copyText } from "src/sections/common/ux/CopyButton";
import { notify } from "src/sections/common/ux/toast";
import {
  HARNESS_FILE,
  harnessScript,
  steps as harnessSteps,
} from "../snippets/prototypeHarness";
import ScriptBlock, { useCodeWrap } from "../ScriptBlock";
import { RECORD_SENTENCES, RECORD_WORDS, recordKeyOf } from "./recipeModel";

const TOP_CENTER = { vertical: "top", horizontal: "center" };

export const LIVE_CONNECTION = "Live connection";

/** The line under the tab (options-truth L2 N2). */
export function pathTabSubs({ entry } = {}) {
  return {
    upload: `You run ${HARNESS_FILE}; it uploads each try`,
    connect: null,
    name: entry?.name ?? null,
  };
}

/** The tab: `Run on your machine` (one line under it). */
export function PathTabsBar({
  form,
  onForm,
  label = "How to run it",
  entry,
  kind,
}) {
  const subs = pathTabSubs({ kind, entry, label });
  const tabs = [{ id: "upload", label: VOCAB.TRACE_UPLOAD, sub: subs.upload }];
  return (
    <Box
      role="tablist"
      aria-label={label}
      sx={{
        display: "grid",
        gridTemplateColumns: "minmax(0, 1fr)",
        gap: 1,
      }}
    >
      {tabs.map((t) => {
        const on = form === t.id;
        return (
          <Box
            key={t.id}
            role="tab"
            tabIndex={0}
            aria-selected={on}
            data-testid={`harness-tab-${t.id}`}
            onClick={() => onForm(t.id)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onForm(t.id);
              }
            }}
            sx={{
              cursor: "pointer",
              minWidth: 0,
              px: 1.25,
              py: 0.5,
              borderRadius: 0.75,
              border: "1px solid",
              borderColor: on ? "primary.main" : "divider",
              bgcolor: on ? "purple.o5" : "background.paper",
              outline: "none",
              "&:focus-visible": {
                boxShadow: (th) => `0 0 0 2px ${th.palette.primary.main}`,
              },
            }}
          >
            <Typography typography="s1" fontWeight="fontWeightSemiBold">
              {t.label}
            </Typography>
            <Typography typography="s3" color="text.secondary" noWrap>
              {t.sub}
            </Typography>
          </Box>
        );
      })}
    </Box>
  );
}
PathTabsBar.propTypes = {
  form: PropTypes.string.isRequired,
  onForm: PropTypes.func.isRequired,
  label: PropTypes.string,
  entry: PropTypes.object,
  /** "harness" | "voice" (defaults from the entry, then the label). */
  kind: PropTypes.oneOf(["harness"]),
};

export function StepBlock({ step, wrap }) {
  return (
    <Stack spacing={0.5} data-step={step.id}>
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        sx={{ minWidth: 0 }}
      >
        <Typography
          typography="s2"
          color="text.secondary"
          sx={{ width: 16, flexShrink: 0 }}
        >
          {step.n}
        </Typography>
        <Typography
          typography="s2"
          fontWeight="fontWeightMedium"
          noWrap
          sx={{ fontFamily: step.fileName ? "monospace" : undefined }}
        >
          {step.title}
        </Typography>
        {step.note ? (
          <Typography
            typography="s3"
            color="text.secondary"
            noWrap
            sx={{ minWidth: 0 }}
          >
            {`· ${step.note}`}
          </Typography>
        ) : null}
      </Stack>
      {step.code ? (
        <Box sx={{ pl: 2.75 }}>
          <ScriptBlock
            text={step.code}
            copyText={step.copyText}
            language={step.lang}
            wrap={wrap}
            dense={!step.fileName}
            copyTooltip={
              step.fileName
                ? `Copy ${step.fileName}`
                : step.id === "keys"
                  ? "Copy two export lines"
                  : "Copy"
            }
            testId={`harness-step-${step.id ?? step.n}`}
          />
        </Box>
      ) : null}
    </Stack>
  );
}
StepBlock.propTypes = {
  step: PropTypes.object.isRequired,
  wrap: PropTypes.bool,
};

export default function HarnessPaths({ entry, form, onForm, ids, recipe }) {
  const [wrap] = useCodeWrap();
  const [open, setOpen] = useState(false);
  const live = false;
  const key = recordKeyOf(entry, { mode: "local", form });
  const list = harnessSteps({
    entry,
    form: "upload",
    ids: { ...ids, recipe },
  });
  const copyAll = async () => {
    const text = harnessScript({ entry, ids, recipe });
    const ok = await copyText(text);
    notify(
      ok
        ? `${HARNESS_FILE} copied · ${text.replace(/\n$/, "").split("\n").length} lines`
        : "Couldn't copy. Your browser blocked the clipboard.",
      {
        variant: ok ? "success" : "error",
        key: "copy-harness-script",
        anchorOrigin: TOP_CENTER,
      },
    );
  };
  return (
    <Stack spacing={1} data-testid="harness-paths" data-form={form}>
      <PathTabsBar form={form} onForm={onForm} entry={entry} kind="harness" />
      {live ? null : (
        <>
          <Stack direction="row" alignItems="center" gap={1}>
            <ButtonBase
              onClick={() => setOpen((v) => !v)}
              aria-expanded={open}
              data-testid="get-the-script"
              sx={{
                gap: 0.5,
                px: 0.5,
                py: 0.25,
                borderRadius: 0.5,
                typography: "s1",
                fontWeight: "fontWeightSemiBold",
              }}
            >
              <Iconify
                icon={open ? "mdi:chevron-down" : "mdi:chevron-right"}
                width={18}
              />
              Get the script
            </ButtonBase>
            <Typography
              typography="s2"
              color="text.secondary"
              noWrap
              sx={{ flex: 1, minWidth: 0 }}
            >
              {`Runs ${entry?.name ?? "the CLI"} once per row in a fresh folder and records `}
              <CustomTooltip
                show
                title={RECORD_SENTENCES[key]}
                size="small"
                placement="top"
              >
                <span data-testid="upload-capture">{RECORD_WORDS[key]}</span>
              </CustomTooltip>
            </Typography>
            <Button
              size="small"
              variant="outlined"
              onClick={copyAll}
              startIcon={<Iconify icon="mdi:content-copy" width={14} />}
              data-testid="copy-harness-script"
              sx={{ typography: "s2", flexShrink: 0 }}
            >
              Copy complete script
            </Button>
          </Stack>
          <Collapse in={open} unmountOnExit>
            <Stack spacing={1.25} sx={{ pl: 1 }}>
              {list.map((s) => (
                <StepBlock key={s.id ?? s.n} step={s} wrap={wrap} />
              ))}
            </Stack>
          </Collapse>
        </>
      )}
    </Stack>
  );
}

HarnessPaths.propTypes = {
  entry: PropTypes.object,
  form: PropTypes.string,
  onForm: PropTypes.func.isRequired,
  ids: PropTypes.object,
  recipe: PropTypes.object,
  dataset: PropTypes.object,
  connectedAgentId: PropTypes.string,
  /** C9: (agent, machine) once the live connection is up (binds the draft). */
  onConnected: PropTypes.func,
};
