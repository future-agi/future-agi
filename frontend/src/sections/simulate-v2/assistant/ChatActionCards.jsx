import PropTypes from "prop-types";
import { Box, Stack, Typography, Button } from "@mui/material";
import Iconify from "src/components/iconify";
import { VERDICT_TONE } from "../components/primitives";
import { SafeTargetPrompt } from "../workspace/ToolDial";
import {
  SecretRefText,
  SecretRefEditor,
  PreflightLine,
} from "../workspace/SecretRefControls";
import { envVarCheckOf } from "../_mock/envConfig";
import { clickable, focusRing } from "../components/clickable";

const MONO = "ui-monospace, Menlo, monospace";

/**
 * What the builder chat answers with when it acts or looks something up —
 * compact cards, not paragraphs (AC-15.5). The same frame as the chat's
 * AskUserQuestion card, so a reply that changed something reads as part of
 * the conversation; every card ends in links to where the thing lives.
 */
export default function ChatActionCard({ turnId, card, actions, envState }) {
  switch (card.type) {
    case "tools":
      return <ToolsCard card={card} actions={actions} />;
    case "failure":
      return <FailureCard card={card} actions={actions} />;
    case "trace":
      return <TraceCard card={card} actions={actions} />;
    case "missing":
      return <MissingCard card={card} actions={actions} />;
    case "envVars":
      return <EnvVarsCard card={card} actions={actions} envState={envState} />;
    case "change":
      return <ChangeCard turnId={turnId} card={card} actions={actions} />;
    case "liveConfirm":
      return <LiveConfirmCard turnId={turnId} card={card} actions={actions} />;
    case "secret":
      return (
        <SecretCard
          turnId={turnId}
          card={card}
          actions={actions}
          envState={envState}
        />
      );
    default:
      return <NoteLine text={card.text} />;
  }
}
ChatActionCard.propTypes = {
  turnId: PropTypes.string,
  card: PropTypes.object.isRequired,
  actions: PropTypes.object.isRequired,
  envState: PropTypes.object,
};

/* ── frame ──────────────────────────────────────────────────────────────── */

function Frame({ icon, title, meta, children, footer }) {
  return (
    <Box
      sx={{
        borderRadius: 2,
        border: "1px solid",
        overflow: "hidden",
        bgcolor: "background.paper",
        borderColor: "border.default",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ px: 1.75, py: 1.125 }}
      >
        {icon && (
          <Iconify
            icon={icon}
            width={16}
            sx={{ color: "text.subtitle", flexShrink: 0 }}
          />
        )}
        <Typography
          sx={{ typography: "s2", fontWeight: 600, flex: 1, minWidth: 0 }}
          noWrap
        >
          {title}
        </Typography>
        {meta && (
          <Typography
            noWrap
            sx={{
              typography: "s3",
              color: "text.subtitle",
              flexShrink: 0,
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {meta}
          </Typography>
        )}
      </Stack>
      {children}
      {footer && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.75}
          flexWrap="wrap"
          rowGap={0.5}
          sx={{
            px: 1.75,
            py: 0.875,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          {footer}
        </Stack>
      )}
    </Box>
  );
}
Frame.propTypes = {
  icon: PropTypes.string,
  title: PropTypes.node,
  meta: PropTypes.node,
  children: PropTypes.node,
  footer: PropTypes.node,
};

function Rows({ children }) {
  return (
    <Stack
      divider={
        <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
      }
      sx={{ borderTop: "1px solid", borderColor: "divider" }}
    >
      {children}
    </Stack>
  );
}
Rows.propTypes = { children: PropTypes.node };

function LinkAction({ label, icon, onClick }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.5}
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter") onClick();
      }}
      sx={{
        cursor: "pointer",
        color: "primary.main",
        "&:hover": { textDecoration: "underline" },
      }}
    >
      <Typography sx={{ typography: "s3", fontWeight: 600 }}>
        {label}
      </Typography>
      <Iconify icon={icon || "solar:arrow-right-up-linear"} width={12} />
    </Stack>
  );
}
LinkAction.propTypes = {
  label: PropTypes.string,
  icon: PropTypes.string,
  onClick: PropTypes.func,
};

function NoteLine({ text }) {
  return (
    <Typography
      sx={{ typography: "s2", lineHeight: 1.65, color: "text.primary" }}
    >
      {text}
    </Typography>
  );
}
NoteLine.propTypes = { text: PropTypes.node };

/* A verdict, neutral text with the outcome on its dot — colour on a mark, not on words. */
function VerdictMark({ status }) {
  const tone = VERDICT_TONE[status] || VERDICT_TONE.missing;
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.625}
      sx={{ flexShrink: 0 }}
    >
      <Box
        sx={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          bgcolor: status === "failed" ? "error.main" : tone.color,
        }}
      />
      <Typography
        sx={{ typography: "s3", fontWeight: 600, color: "text.primary" }}
      >
        {tone.label}
      </Typography>
    </Stack>
  );
}
VerdictMark.propTypes = { status: PropTypes.string };

const runLabel = (run) =>
  `Run ${run?.ordinal || ""} · agent ${run?.agentVersion || "v1"}`;

/* ── answers ────────────────────────────────────────────────────────────── */

function ToolsCard({ card, actions }) {
  return (
    <Frame
      icon="solar:widget-4-linear"
      title={`${card.rows.length} tool${card.rows.length === 1 ? "" : "s"} found`}
      meta={card.summary}
      footer={
        <LinkAction
          label={
            card.connectOnly ? "Open Contract" : "Open World · tool fidelity"
          }
          onClick={() =>
            actions.go.tab(card.connectOnly ? "contract" : "world")
          }
        />
      }
    >
      <Rows>
        {card.rows.map((r) => (
          <Stack
            key={r.name}
            direction="row"
            alignItems="baseline"
            spacing={1.25}
            sx={{ px: 1.75, py: 0.75 }}
          >
            <Box flex={1} minWidth={0}>
              <Typography
                noWrap
                sx={{ typography: "s3", fontFamily: MONO, fontWeight: 600 }}
              >
                {r.name}
              </Typography>
              <Typography
                noWrap
                sx={{ typography: "s3", color: "text.subtitle" }}
              >
                {r.faults ? `${r.desc} · faults: ${r.faults}` : r.desc}
              </Typography>
            </Box>
            {r.tier && (
              <Typography
                sx={{
                  typography: "s3",
                  fontWeight: 600,
                  color: r.isDefault ? "text.secondary" : "text.primary",
                  flexShrink: 0,
                }}
              >
                {r.tier}
                {r.isDefault ? "" : " · set"}
              </Typography>
            )}
          </Stack>
        ))}
      </Rows>
    </Frame>
  );
}
ToolsCard.propTypes = { card: PropTypes.object, actions: PropTypes.object };

function FailureCard({ card, actions }) {
  if (card.empty) return <NoteLine text={card.empty} />;
  return (
    <Frame
      icon="solar:document-text-linear"
      title={card.name}
      meta={runLabel(card.run)}
      footer={
        <>
          {!card.passed && (
            <LinkAction
              label="Open trace"
              onClick={() => actions.go.run(card.run.id, card.scenarioId)}
            />
          )}
          <LinkAction
            label="Open scenario"
            onClick={() => actions.go.scenario(card.scenarioId)}
          />
          <LinkAction
            label="Open run"
            onClick={() => actions.go.run(card.run.id)}
          />
        </>
      }
    >
      <Stack spacing={0.75} sx={{ px: 1.75, pb: 1.25 }}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <VerdictMark status={card.status} />
          {card.samples && (
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {card.samples}
            </Typography>
          )}
        </Stack>
        {card.summary && (
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {card.summary}
          </Typography>
        )}
        {card.passed ? (
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            It passed in this run — nothing to explain.
          </Typography>
        ) : (
          card.verdict && (
            <Box
              sx={{
                px: 1.25,
                py: 1,
                borderRadius: 1,
                bgcolor: "background.neutral",
              }}
            >
              <Typography
                sx={{
                  typography: "s3",
                  fontWeight: 600,
                  color: "text.secondary",
                  mb: 0.25,
                }}
              >
                {card.verdict.source}
              </Typography>
              <Typography
                sx={{
                  typography: "s2",
                  color: "text.primary",
                  lineHeight: 1.55,
                }}
              >
                {card.verdict.line}
              </Typography>
            </Box>
          )
        )}
        {card.faults?.length > 0 && (
          <Stack spacing={0.25}>
            {card.faults.map((f, i) => (
              <Typography
                key={`${f.tool}-${i}`}
                sx={{ typography: "s3", color: "text.secondary" }}
              >
                <Box
                  component="span"
                  sx={{ fontFamily: MONO, color: "text.primary" }}
                >
                  {f.tool}
                </Box>{" "}
                · {f.note}
              </Typography>
            ))}
          </Stack>
        )}
      </Stack>
    </Frame>
  );
}
FailureCard.propTypes = { card: PropTypes.object, actions: PropTypes.object };

function TraceCard({ card, actions }) {
  if (card.empty) return <NoteLine text={card.empty} />;
  return (
    <Frame
      icon="solar:routing-2-linear"
      title={`Trace · ${card.name}`}
      meta={runLabel(card.run)}
      footer={
        <LinkAction
          label="Open the trace"
          onClick={() => actions.go.run(card.run.id, card.scenarioId)}
        />
      }
    >
      <Stack spacing={0.5} sx={{ px: 1.75, pb: 1.25 }}>
        <VerdictMark status={card.status} />
        {card.verdict && (
          <Typography
            sx={{ typography: "s2", color: "text.secondary", lineHeight: 1.55 }}
          >
            <Box
              component="span"
              sx={{ fontWeight: 600, color: "text.primary" }}
            >
              {card.verdict.source}:
            </Box>{" "}
            {card.verdict.line}
          </Typography>
        )}
      </Stack>
    </Frame>
  );
}
TraceCard.propTypes = { card: PropTypes.object, actions: PropTypes.object };

function MissingCard({ card, actions }) {
  const open = card.blocking.length + card.optional.length;
  return (
    <Frame
      icon="solar:checklist-minimalistic-linear"
      title={card.ready ? "Ready to run" : "Not ready to run"}
      meta={`${card.blocking.length} blocking · ${card.optional.length} optional`}
      footer={
        <>
          <LinkAction
            label="Open Needs input"
            onClick={() => actions.go.gaps()}
          />
          {card.credentials.length > 0 && (
            <LinkAction
              label="Open Settings"
              onClick={() => actions.go.tab("settings")}
            />
          )}
        </>
      }
    >
      {!card.ready && card.reason && (
        <Typography
          sx={{ typography: "s2", color: "text.primary", px: 1.75, pb: 1 }}
        >
          {card.reason}
        </Typography>
      )}
      {open + card.credentials.length > 0 ? (
        <Rows>
          {card.blocking.map((g) => (
            <GapRow
              key={g.id}
              label="Blocking"
              title={g.title}
              area={g.area}
              strong
              onClick={() => actions.go.gaps(g.id)}
            />
          ))}
          {card.optional.map((g) => (
            <GapRow
              key={g.id}
              label="Optional"
              title={g.title}
              area={g.area}
              onClick={() => actions.go.gaps(g.id)}
            />
          ))}
          {card.credentials.map((c) => (
            <GapRow
              key={c.id}
              label="Not set"
              title={c.id}
              mono
              area="Credential"
              onClick={() => actions.go.gaps()}
            />
          ))}
        </Rows>
      ) : (
        <Typography
          sx={{ typography: "s3", color: "text.subtitle", px: 1.75, pb: 1.25 }}
        >
          Nothing open — every input is answered or assumed.
        </Typography>
      )}
    </Frame>
  );
}
MissingCard.propTypes = { card: PropTypes.object, actions: PropTypes.object };

function GapRow({ label, title, area, strong, mono, onClick }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.25}
      {...clickable(onClick)}
      sx={{
        ...focusRing,
        px: 1.75,
        py: 0.75,
        cursor: "pointer",
        "&:hover": { bgcolor: "action.hover" },
      }}
    >
      <Typography
        sx={{
          typography: "s3",
          fontWeight: 600,
          width: 58,
          flexShrink: 0,
          color: strong ? "error.main" : "text.subtitle",
        }}
      >
        {label}
      </Typography>
      <Typography
        noWrap
        sx={{
          typography: "s3",
          flex: 1,
          minWidth: 0,
          color: "text.primary",
          ...(mono && { fontFamily: MONO }),
        }}
      >
        {title}
      </Typography>
      <Typography
        noWrap
        sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}
      >
        {area}
      </Typography>
    </Stack>
  );
}
GapRow.propTypes = {
  label: PropTypes.string,
  title: PropTypes.string,
  area: PropTypes.string,
  strong: PropTypes.bool,
  mono: PropTypes.bool,
  onClick: PropTypes.func,
};

function EnvVarsCard({ card, actions }) {
  return (
    <Frame
      icon="solar:settings-linear"
      title={
        card.usedBy
          ? `${card.rows.length} variable${card.rows.length === 1 ? "" : "s"} the ${card.usedBy} reads`
          : `${card.rows.length} environment variables`
      }
      footer={
        <LinkAction
          label="Open Settings"
          onClick={() => actions.go.tab("settings")}
        />
      }
    >
      {card.rows.length ? (
        <Rows>
          {card.rows.map((v) => (
            <Stack
              key={v.key}
              direction="row"
              alignItems="center"
              spacing={1.25}
              sx={{ px: 1.75, py: 0.75 }}
            >
              <Typography
                noWrap
                sx={{
                  typography: "s3",
                  fontFamily: MONO,
                  fontWeight: 600,
                  width: 150,
                  flexShrink: 0,
                }}
              >
                {v.key}
              </Typography>
              {v.secret ? (
                <SecretRefText
                  value={v.ref}
                  sx={{ typography: "s3", flex: 1 }}
                />
              ) : (
                <Typography
                  noWrap
                  sx={{
                    typography: "s3",
                    fontFamily: MONO,
                    color: "text.secondary",
                    flex: 1,
                    minWidth: 0,
                  }}
                >
                  {v.value}
                </Typography>
              )}
            </Stack>
          ))}
        </Rows>
      ) : (
        <Typography
          sx={{ typography: "s3", color: "text.subtitle", px: 1.75, pb: 1.25 }}
        >
          None.
        </Typography>
      )}
    </Frame>
  );
}
EnvVarsCard.propTypes = { card: PropTypes.object, actions: PropTypes.object };

/* ── changes ────────────────────────────────────────────────────────────── */

/* The review/undo strip every applied change ends in (AC-19.8). */
function ChangeCard({ turnId, card, actions }) {
  const reviewLabel =
    { world: "Review on World", evals: "Review on Evaluations" }[card.review] ||
    "Review";
  return (
    <Frame
      icon={
        card.undone
          ? "solar:undo-left-round-linear"
          : "solar:check-circle-linear"
      }
      title={card.undone ? `Undone — ${card.title}` : card.title}
      footer={
        card.undone ? (
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            Put back as it was. Nothing was recorded.
          </Typography>
        ) : (
          <>
            <LinkAction
              label={reviewLabel}
              onClick={() => actions.go.tab(card.review)}
            />
            {card.undo && (
              <Button
                size="small"
                onClick={() => actions.undo(turnId, card)}
                startIcon={
                  <Iconify icon="solar:undo-left-round-linear" width={14} />
                }
                sx={{
                  color: "text.secondary",
                  minWidth: 0,
                  ml: "auto !important",
                }}
              >
                Undo
              </Button>
            )}
          </>
        )
      }
    >
      {!card.undone && card.line && (
        <Typography
          sx={{
            typography: "s3",
            color: "text.secondary",
            px: 1.75,
            pb: 1.125,
            lineHeight: 1.55,
          }}
        >
          {card.line}
        </Typography>
      )}
    </Frame>
  );
}
ChangeCard.propTypes = {
  turnId: PropTypes.string,
  card: PropTypes.object,
  actions: PropTypes.object,
};

/*
  Live is the real tool. Nothing changes until someone names a safe target
  — the same prompt the World tab and the setup inbox use.
*/
function LiveConfirmCard({ turnId, card, actions }) {
  const { plan } = card;
  if (card.status === "applied") {
    return (
      <ChangeCard
        turnId={turnId}
        actions={actions}
        card={{
          ...card,
          title: `${plan.tool}: ${plan.from} → Live`,
          line: card.safeTarget
            ? `Runs call the real ${plan.tool} at ${card.safeTarget}. Recorded as a world edit; results on Live aren't deterministic.`
            : `Runs call the real ${plan.tool} in the sandbox. Recorded as a world edit.`,
          undoKind: "tool",
          review: "world",
        }}
      />
    );
  }
  if (card.status === "declined") {
    return (
      <NoteLine text={`Left ${plan.tool} on ${plan.from}. Nothing changed.`} />
    );
  }
  return (
    <Frame
      icon="solar:danger-triangle-linear"
      title={`Set ${plan.tool} to Live?`}
      meta={`now ${plan.from}`}
    >
      <Stack spacing={1.25} sx={{ px: 1.75, pb: 1.5 }}>
        <Typography
          sx={{ typography: "s2", color: "text.primary", lineHeight: 1.55 }}
        >
          Live runs the real tool in the sandbox — confirm a safe target first.
          Results stop being deterministic, and a target that isn&apos;t a
          sandbox would be production.
        </Typography>
        {plan.needsTarget ? (
          <SafeTargetPrompt
            bare
            setting={plan.setting}
            onConfirm={(url) => actions.confirmLive(turnId, plan, url)}
            onDowngrade={() => actions.declineLive(turnId)}
          />
        ) : (
          <Stack direction="row" spacing={1}>
            <Button
              size="small"
              variant="outlined"
              onClick={() => actions.confirmLive(turnId, plan, null)}
              sx={{ color: "text.primary", borderColor: "divider" }}
            >
              Set to Live
            </Button>
            <Button
              size="small"
              onClick={() => actions.declineLive(turnId)}
              sx={{ color: "text.secondary" }}
            >
              Keep {plan.from}
            </Button>
          </Stack>
        )}
      </Stack>
    </Frame>
  );
}
LiveConfirmCard.propTypes = {
  turnId: PropTypes.string,
  card: PropTypes.object,
  actions: PropTypes.object,
};

/* Point a secret at a new reference, here in the chat; the access check runs as it does on Settings. */
function SecretCard({ turnId, card, actions, envState }) {
  const { target } = card;
  const check =
    target.type === "env"
      ? envVarCheckOf(envState, target.id)
      : envState?.secretChecks?.[target.id] || null;
  if (card.status === "cancelled")
    return <NoteLine text={`Left ${target.id} pointing where it was.`} />;
  return (
    <Frame
      icon="solar:key-minimalistic-linear"
      title={target.id}
      meta={target.type === "env" ? "environment variable" : "agent credential"}
      footer={
        <LinkAction
          label={target.type === "env" ? "Open Settings" : "Open Needs input"}
          onClick={() =>
            target.type === "env"
              ? actions.go.tab("settings")
              : actions.go.gaps()
          }
        />
      }
    >
      <Stack spacing={1} sx={{ px: 1.75, pb: 1.5 }}>
        <Stack direction="row" alignItems="baseline" spacing={1} minWidth={0}>
          <Typography
            sx={{
              typography: "s3",
              color: "text.subtitle",
              flexShrink: 0,
              width: 54,
            }}
          >
            {card.status === "saved" ? "Now" : "Currently"}
          </Typography>
          <SecretRefText
            value={card.status === "saved" ? card.ref : target.ref}
            sx={{ typography: "s3" }}
          />
        </Stack>
        {card.status === "saved" ? (
          <PreflightLine check={check?.ref === card.ref ? check : null} />
        ) : (
          <>
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              Paste the new key&apos;s reference. Only the access check re-runs
              — nothing is rebuilt and no version is created.
            </Typography>
            <SecretRefEditor
              autoFocus={false}
              placeholder={
                target.ref || `vault://acme/sim/${target.id.toLowerCase()}`
              }
              onSave={(ref) => actions.setSecret(turnId, target, ref)}
              onCancel={() => actions.cancelSecret(turnId)}
            />
          </>
        )}
      </Stack>
    </Frame>
  );
}
SecretCard.propTypes = {
  turnId: PropTypes.string,
  card: PropTypes.object,
  actions: PropTypes.object,
  envState: PropTypes.object,
};
