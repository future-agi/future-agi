import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import SectionCard from "../../components/SectionCard";
import SoonChip from "../../components/SoonChip";
import ToolArgsGrid from "../world/ToolArgsGrid";

const MONO = "ui-monospace, Menlo, monospace";

// A use case's id, derived from its text the way Vel's are (UC-xxxx), so the
// same use case keeps its id wherever it's listed.
export const idOfUseCase = (text) => {
  let h = 0;
  for (const ch of String(text || "").slice(0, 40)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return `UC-${h.toString(36).slice(-4).padStart(4, "0")}`;
};

export function AddButton({ label, onClick }) {
  return (
    <Button
      size="small"
      onClick={onClick}
      startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
      sx={{ color: "text.secondary", whiteSpace: "nowrap", flexShrink: 0 }}
    >
      {label}
    </Button>
  );
}
AddButton.propTypes = { label: PropTypes.string, onClick: PropTypes.func };

function RowActions({ onEdit, onRemove }) {
  return (
    <Stack direction="row" spacing={0.25} className="row-edit" sx={{ opacity: 0.6, transition: "opacity 120ms", alignSelf: "center" }}>
      <Button size="small" onClick={onEdit} sx={{ minWidth: 0, color: "text.secondary" }}>
        Edit
      </Button>
      <Button size="small" onClick={onRemove} sx={{ minWidth: 0, color: "text.secondary" }}>
        Remove
      </Button>
    </Stack>
  );
}
RowActions.propTypes = { onEdit: PropTypes.func, onRemove: PropTypes.func };

// Vel's Rules: each hard constraint with a stable id and where it came from.
// Which sub-goals test a rule isn't reported, so coverage is a soon chip.
export function RulesSection({ rules, onEdit }) {
  return (
    <SectionCard
      title={
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <span>{`Rules (${rules.length})`}</span>
          <SoonChip label="Coverage" detail="For each rule, the sub-goals and scenarios that test it, so an untested rule stands out." what="Which sub-goals and scenarios test each rule" size="tiny" />
        </Stack>
      }
      subtitle="From the agent's prompt. When a new agent version changes one, it's either a fix, and the suite stays, or a spec change, and the rule, its sub-goals and their scenarios change. Someone confirms which."
      action={onEdit && <AddButton label="Add rule" onClick={() => onEdit({ target: "rule", op: "add", text: "" })} />}
      sx={{ mb: 2 }}
    >
      <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
        {rules.length === 0 && (
          <Typography sx={{ typography: "s2", color: "text.subtitle", px: 2.5, py: 1.5 }}>
            No rules read from this agent.
          </Typography>
        )}
        {rules.map((text, i) => {
          const id = `R${i + 1}`;
          return (
            <Box
              key={id}
              sx={{
                display: "grid",
                gridTemplateColumns: onEdit ? "36px 1fr auto" : "36px 1fr",
                columnGap: 1.5,
                px: 2.5,
                py: 1.25,
                alignItems: "baseline",
                "&:hover .row-edit": { opacity: 1 },
              }}
            >
              <Typography sx={{ typography: "s3", fontFamily: MONO, fontWeight: 600, color: "text.subtitle" }}>{id}</Typography>
              <Box minWidth={0}>
                <Typography sx={{ typography: "s2", fontWeight: 600 }}>{text}</Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>From the agent&apos;s prompt</Typography>
              </Box>
              {onEdit && (
                <RowActions
                  onEdit={() => onEdit({ target: "rule", op: "edit", id, text })}
                  onRemove={() => onEdit({ target: "rule", op: "remove", id, text })}
                />
              )}
            </Box>
          );
        })}
      </Stack>
    </SectionCard>
  );
}
RulesSection.propTypes = { rules: PropTypes.arrayOf(PropTypes.string).isRequired, onEdit: PropTypes.func };

// Vel's Argument limits: the values a tool call has to stay inside. Our
// contract reports allowed values (arg_values); numeric ranges aren't.
export function ArgLimitsSection({ limits, onEdit, onGo }) {
  return (
    <SectionCard
      title={
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <span>{`Argument limits (${limits.length})`}</span>
          <SoonChip label="Ranges" detail="For number arguments, the lowest and highest value a tool call may use. A call outside them counts as the agent's mistake." what="Lowest and highest values for number arguments" plural size="tiny" />
        </Stack>
      }
      subtitle="Values a tool call has to stay inside. A call outside them is the agent's mistake, not the world's. Every tool's arguments are on World → Tools."
      action={
        onGo && (
          <Button size="small" onClick={() => onGo("world")} sx={{ color: "text.secondary", flexShrink: 0, whiteSpace: "nowrap" }}>
            View tools
          </Button>
        )
      }
      sx={{ mb: 2 }}
    >
      <Box sx={{ px: 2.5, py: 1.5 }}>
        <ToolArgsGrid args={limits} rowGap={1} onEdit={onEdit ? (a) => onEdit({ target: "toolArg", arg: a }) : null} />
      </Box>
    </SectionCard>
  );
}
ArgLimitsSection.propTypes = { limits: PropTypes.array.isRequired, onEdit: PropTypes.func, onGo: PropTypes.func };

// Vel's Use cases: what the agent is for, each with an id.
export function UseCasesSection({ useCases, onEdit }) {
  return (
    <SectionCard
      title={`Use cases (${useCases.length})`}
      subtitle="What the agent is for, read from its prompt."
      action={onEdit && <AddButton label="Add use case" onClick={() => onEdit({ target: "useCase", op: "add", text: "" })} />}
      sx={{ mb: 2 }}
    >
      <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
        {useCases.map((text, i) => {
          const id = idOfUseCase(text);
          return (
            <Box
              key={`${id}:${i}`}
              sx={{
                display: "grid",
                gridTemplateColumns: onEdit ? "72px 1fr auto" : "72px 1fr",
                columnGap: 1.5,
                px: 2.5,
                py: 1.25,
                alignItems: "baseline",
                "&:hover .row-edit": { opacity: 1 },
              }}
            >
              <Typography sx={{ typography: "s3", fontFamily: MONO, color: "text.subtitle" }}>{id}</Typography>
              <Typography sx={{ typography: "s2", minWidth: 0 }}>{text}</Typography>
              {onEdit && (
                <RowActions
                  onEdit={() => onEdit({ target: "useCase", op: "edit", id, text })}
                  onRemove={() => onEdit({ target: "useCase", op: "remove", id, text })}
                />
              )}
            </Box>
          );
        })}
      </Stack>
    </SectionCard>
  );
}
UseCasesSection.propTypes = { useCases: PropTypes.arrayOf(PropTypes.string).isRequired, onEdit: PropTypes.func };
