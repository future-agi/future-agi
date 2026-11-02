import PropTypes from "prop-types";
import { useState } from "react";
import { Box, ButtonBase, Collapse, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import SectionCard from "../../components/SectionCard";
import SoonChip from "../../components/SoonChip";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import { SAMPLE_SCENARIO_CHECKS } from "src/api/simulate-environments/_fixtures/detail/world";
import CodeBlock from "./CodeBlock";

const MONO = "ui-monospace, Menlo, monospace";

// Vel's "Scenario checks": what proves each scenario before it runs, folded
// to one line until asked for. The checks aren't in the API, so a real
// environment shows the folded line with a soon chip; `sample` lists Vel's.
export default function ScenarioChecksCard({ sample, onGo }) {
  const [shown, setShown] = useState(false);
  const [openCheck, setOpenCheck] = useState(null);

  if (!sample || !shown) {
    return (
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        {...(sample && {
          role: "button",
          tabIndex: 0,
          "aria-expanded": false,
          onClick: () => setShown(true),
          onKeyDown: (e) => (e.key === "Enter" || e.key === " ") && setShown(true),
        })}
        sx={{
          px: 2,
          py: 1.25,
          borderRadius: 1,
          border: "1px solid",
          borderColor: "divider",
          cursor: sample ? "pointer" : "default",
          "&:hover": sample ? { bgcolor: "action.hover" } : {},
        }}
      >
        <Iconify icon="solar:shield-check-linear" width={16} sx={{ color: "text.subtitle" }} />
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {sample ? `Scenario checks · ${SAMPLE_SCENARIO_CHECKS.length}` : "Scenario checks"}
        </Typography>
        {sample ? (
          <UiOnlyFlag feature="scenarioChecks" />
        ) : (
          <SoonChip label="Checks" detail="Before a scenario counts, it is proved: setup stages its data, a known-good run passes, an empty run fails, and wrong actions are caught." what="The checks that prove each scenario before it runs" plural size="tiny" />
        )}
        <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}>
          What proves each scenario before it runs
        </Typography>
        {sample && <Iconify icon="solar:alt-arrow-down-linear" width={14} sx={{ color: "text.subtitle" }} />}
      </Stack>
    );
  }

  return (
    <SectionCard
      title={
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <span>Scenario checks</span>
          <UiOnlyFlag feature="scenarioChecks" />
        </Stack>
      }
      subtitle="What proves each scenario before it runs. On a run, sub-goals decide pass or fail; evaluations score each conversation"
      action={
        onGo && (
          <ButtonBase
            onClick={() => onGo("evals")}
            sx={{ typography: "s3", color: "primary.main", fontWeight: 600, borderRadius: "4px" }}
          >
            Evaluations
          </ButtonBase>
        )
      }
    >
      <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
        {SAMPLE_SCENARIO_CHECKS.map((check) => {
          const open = openCheck === check.id;
          const toggle = () => setOpenCheck(open ? null : check.id);
          return (
            <Box key={check.id}>
              <Stack
                direction="row"
                alignItems="center"
                spacing={1.5}
                role="button"
                tabIndex={0}
                aria-expanded={open}
                onClick={toggle}
                onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && toggle()}
                sx={{ px: 2.5, py: 1.375, cursor: "pointer", "&:hover": { bgcolor: "action.hover" } }}
              >
                <Iconify
                  icon={open ? "solar:alt-arrow-down-linear" : "solar:alt-arrow-right-linear"}
                  width={14}
                  sx={{ color: "text.subtitle", flexShrink: 0 }}
                />
                <Iconify icon="solar:shield-check-linear" width={16} sx={{ color: "text.subtitle", flexShrink: 0 }} />
                <Typography sx={{ typography: "s2", fontWeight: 600, fontFamily: MONO, minWidth: 220 }}>{check.name}</Typography>
                <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", flex: 1, minWidth: 0 }}>
                  {check.desc}
                </Typography>
              </Stack>
              <Collapse in={open}>
                <CodeBlock file={check.file} code={check.code} />
              </Collapse>
            </Box>
          );
        })}
      </Stack>
    </SectionCard>
  );
}
ScenarioChecksCard.propTypes = { sample: PropTypes.bool, onGo: PropTypes.func };
