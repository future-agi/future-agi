import PropTypes from "prop-types";
import { Box, Button, Skeleton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import SectionCard from "../components/SectionCard";
import { TAB_SKELETON_COPY as C } from "./workspace.constants";

const Bar = ({ width, height = 12, sx }) => (
  <Skeleton variant="rounded" width={width} height={height} sx={sx} />
);

Bar.propTypes = {
  width: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
  height: PropTypes.number,
  sx: PropTypes.object,
};

const rowDivider = (
  <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
);

function HeldButton({ label, icon = "solar:add-circle-linear", contained }) {
  return (
    <Button
      size="small"
      disabled
      variant={contained ? "contained" : "outlined"}
      startIcon={icon ? <Iconify icon={icon} width={14} /> : null}
      sx={{ flexShrink: 0 }}
    >
      {label}
    </Button>
  );
}

HeldButton.propTypes = {
  label: PropTypes.string.isRequired,
  icon: PropTypes.string,
  contained: PropTypes.bool,
};

function Intro({ action }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      justifyContent="space-between"
      spacing={2}
      sx={{ mb: 1.5 }}
    >
      <Bar width="42%" />
      {action}
    </Stack>
  );
}

Intro.propTypes = { action: PropTypes.node };

const subtitleBar = <Bar width={160} height={10} sx={{ mt: 0.5 }} />;

function WorldSkeleton() {
  return (
    <>
      <Intro />
      <SectionCard
        title={C.world.graph}
        subtitle={C.world.graphSub}
        sx={{ mb: 2 }}
      />
      <SectionCard title={C.world.tools} subtitle={subtitleBar}>
        <Box sx={{ px: 1.5, py: 0.75, bgcolor: "background.neutral" }}>
          <Bar width="38%" height={10} />
        </Box>
        <Stack divider={rowDivider}>
          {[
            ["18%", "52%"],
            ["14%", "44%"],
            ["16%", "58%"],
            ["20%", "40%"],
            ["12%", "48%"],
          ].map(([name, text]) => (
            <Stack
              key={`${name}${text}`}
              direction="row"
              spacing={3}
              alignItems="center"
              sx={{ px: 1.5, py: 1.25 }}
            >
              <Bar width={name} />
              <Bar width={text} />
            </Stack>
          ))}
        </Stack>
      </SectionCard>
    </>
  );
}

function NumberedCard({ title, add, prefix, rows }) {
  return (
    <SectionCard
      roomy
      title={title}
      subtitle={subtitleBar}
      action={<HeldButton label={add} />}
      sx={{ mb: 2 }}
    >
      <Stack divider={rowDivider}>
        {rows.map((width, index) => (
          <Stack
            key={width}
            direction="row"
            alignItems="center"
            spacing={4}
            sx={{ px: 2.5, py: 1.5 }}
          >
            <Typography
              sx={{ typography: "s3", color: "text.disabled", width: 20 }}
            >{`${prefix}${index + 1}`}</Typography>
            <Bar width={width} />
          </Stack>
        ))}
      </Stack>
    </SectionCard>
  );
}

NumberedCard.propTypes = {
  title: PropTypes.string.isRequired,
  add: PropTypes.string.isRequired,
  prefix: PropTypes.string.isRequired,
  rows: PropTypes.arrayOf(PropTypes.string).isRequired,
};

function ContractSkeleton() {
  return (
    <>
      <NumberedCard
        title={C.contract.rules}
        add={C.contract.addRule}
        prefix="R"
        rows={["64%", "78%", "52%", "70%", "46%"]}
      />
      <NumberedCard
        title={C.contract.useCases}
        add={C.contract.addUseCase}
        prefix="U"
        rows={["58%", "44%", "62%"]}
      />
    </>
  );
}

function GroupBand({ label }) {
  return (
    <Box
      sx={{
        px: 1.5,
        py: 0.75,
        bgcolor: "background.neutral",
        borderBottom: "1px solid",
        borderColor: "divider",
      }}
    >
      <Typography sx={{ typography: "s3", fontWeight: 600 }}>
        {label}
      </Typography>
    </Box>
  );
}

GroupBand.propTypes = { label: PropTypes.string.isRequired };

function ActorLine({ name, line }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.5}
      sx={{ px: 1.5, py: 1 }}
    >
      <Box
        sx={{
          width: 28,
          height: 28,
          borderRadius: "6px",
          bgcolor: "background.neutral",
          flexShrink: 0,
        }}
      />
      <Stack spacing={0.75} sx={{ flex: 1, minWidth: 0 }}>
        <Bar width={name} />
        <Bar width={line} height={10} />
      </Stack>
      <Bar width={96} height={10} />
    </Stack>
  );
}

ActorLine.propTypes = {
  name: PropTypes.string.isRequired,
  line: PropTypes.string.isRequired,
};

function ActorsSkeleton() {
  return (
    <>
      <Intro action={<HeldButton label={C.actors.add} />} />
      <SectionCard
        title={C.actors.title}
        subtitle={C.actors.everyRun}
        sx={{ mb: 2 }}
      >
        <GroupBand label={C.actors.callerSide} />
        <ActorLine name="34%" line="46%" />
        <ActorLine name="28%" line="40%" />
        <GroupBand label={C.actors.inWorld} />
        <ActorLine name="30%" line="52%" />
      </SectionCard>
      <SectionCard title={C.actors.connect} subtitle={C.actors.connectSub}>
        <Stack divider={rowDivider}>
          {["22%", "18%", "26%"].map((width) => (
            <Stack
              key={width}
              direction="row"
              spacing={6}
              sx={{ px: 1.5, py: 1.25 }}
            >
              <Bar width={width} />
              <Bar width="20%" />
            </Stack>
          ))}
        </Stack>
      </SectionCard>
    </>
  );
}

const SCENARIO_COLUMNS = [
  { key: "pick", width: 40 },
  { key: "no", width: 48 },
  { key: "scenario", width: "30%" },
  { key: "persona", width: "22%" },
  { key: "situation", width: "auto" },
];

function ScenarioRow({ widths }) {
  return (
    <Stack direction="row" alignItems="center" sx={{ px: 1.5, py: 1.5 }}>
      <Box sx={{ width: 40 }}>
        <Box
          sx={{
            width: 14,
            height: 14,
            borderRadius: "3px",
            border: "1px solid",
            borderColor: "divider",
          }}
        />
      </Box>
      <Box sx={{ width: 48 }}>
        <Bar width={10} />
      </Box>
      <Box sx={{ width: "30%" }}>
        <Bar width={widths[0]} />
      </Box>
      <Stack spacing={0.5} sx={{ width: "22%" }}>
        <Bar width={widths[1]} />
        <Bar width="40%" height={10} />
      </Stack>
      <Stack spacing={0.5} sx={{ flex: 1 }}>
        <Bar width="90%" />
        <Bar width={widths[2]} />
      </Stack>
    </Stack>
  );
}

ScenarioRow.propTypes = {
  widths: PropTypes.arrayOf(PropTypes.string).isRequired,
};

function ScenariosSkeleton() {
  return (
    <>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1.5 }}>
        <Box
          sx={{
            flex: 1,
            maxWidth: 420,
            height: 30,
            px: 1.5,
            display: "flex",
            alignItems: "center",
            gap: 1,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "6px",
            color: "text.disabled",
          }}
        >
          <Iconify icon="solar:magnifer-linear" width={14} />
          <Typography sx={{ typography: "s2", color: "text.disabled" }}>
            {C.scenarios.search}
          </Typography>
        </Box>
        <HeldButton label={C.scenarios.filter} icon="solar:filter-linear" />
        <HeldButton label={C.scenarios.display} icon="solar:tuning-2-linear" />
        <Box sx={{ flex: 1 }} />
        <HeldButton label={C.scenarios.add} />
      </Stack>
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
          overflow: "hidden",
        }}
      >
        <Stack
          direction="row"
          alignItems="center"
          sx={{
            px: 1.5,
            py: 1,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          {SCENARIO_COLUMNS.map((column) => (
            <Typography
              key={column.key}
              sx={{
                typography: "s3",
                color: "text.secondary",
                width: column.width === "auto" ? undefined : column.width,
                flex: column.width === "auto" ? 1 : undefined,
              }}
            >
              {C.scenarios.columns[column.key]}
            </Typography>
          ))}
        </Stack>
        <Box
          sx={{
            px: 1.5,
            py: 1,
            bgcolor: "background.neutral",
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Bar width="36%" />
        </Box>
        <Stack divider={rowDivider}>
          <ScenarioRow widths={["70%", "56%", "62%"]} />
          <ScenarioRow widths={["58%", "48%", "54%"]} />
          <ScenarioRow widths={["66%", "52%", "46%"]} />
        </Stack>
      </Box>
    </>
  );
}

function EvalLine({ name, text, add }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={2}
      sx={{ px: 1.5, py: 1.25 }}
    >
      <Stack spacing={0.75} sx={{ flex: 1, minWidth: 0 }}>
        <Bar width={name} />
        <Bar width={text} height={10} />
      </Stack>
      <Bar width={44} height={10} />
      {add && <HeldButton label={add} icon={null} />}
    </Stack>
  );
}

EvalLine.propTypes = {
  name: PropTypes.string.isRequired,
  text: PropTypes.string.isRequired,
  add: PropTypes.string,
};

function EvalsSkeleton() {
  return (
    <>
      <SectionCard
        title={C.evals.suggested}
        subtitle={subtitleBar}
        action={<HeldButton label={C.evals.addAll} icon={null} />}
        sx={{ mb: 2 }}
      >
        <Stack divider={rowDivider}>
          <EvalLine name="26%" text="54%" add={C.evals.add} />
          <EvalLine name="20%" text="48%" add={C.evals.add} />
          <EvalLine name="30%" text="58%" add={C.evals.add} />
        </Stack>
      </SectionCard>
      <SectionCard title={C.evals.added} subtitle={subtitleBar}>
        <Stack divider={rowDivider}>
          <EvalLine name="24%" text="50%" />
          <EvalLine name="28%" text="44%" />
        </Stack>
      </SectionCard>
    </>
  );
}

const BY_TAB = {
  world: WorldSkeleton,
  contract: ContractSkeleton,
  actors: ActorsSkeleton,
  scenarios: ScenariosSkeleton,
  evals: EvalsSkeleton,
};

export default function TabSkeleton({ tabId }) {
  const Shape = BY_TAB[tabId];
  if (!Shape) return null;
  return (
    <Box data-testid={`tab-skeleton-${tabId}`}>
      <Shape />
    </Box>
  );
}

TabSkeleton.propTypes = { tabId: PropTypes.string.isRequired };
