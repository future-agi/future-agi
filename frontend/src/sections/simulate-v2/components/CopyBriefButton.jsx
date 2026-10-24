import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  ClickAwayListener,
  Divider,
  Fade,
  Menu,
  MenuItem,
  Popper,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import Section from "src/components/section";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import {
  connectedCloudAgents,
  connectedCodingAgent,
  dispatchToCloudAgent,
  handBriefTo,
  startImproverSession,
  useIntegrations,
} from "../_mock/integrations";
import BrandMark from "../integrations/BrandMark";

/**
 * "Copy for coding agent" — copies a markdown brief (see
 * `_mock/codingAgentBrief`) and says so for a moment.
 *
 * The brief is built on click, not on render: it reads the whole diagnosis,
 * and nobody should pay for that on every re-render of a drawer.
 *
 * With your coding agent connected (Settings → Integrations → IDE) it
 * reads "Open in Claude Code" instead: the click parks the brief for your
 * agent, which pulls it with get_brief the next time you ask — nothing opens
 * on its own, and nothing is copied. Copying stays one click
 * away on the caret. `target` says what the brief is about; an improvement
 * also starts an improver session, so the agent's attempts land there as trials.
 *
 * Cloud agents the workspace connected are one click away as "Dispatch to …":
 * the brief goes to that agent, which works in its own sandbox and sends its
 * change back through submit_candidate — Future AGI runs it and computes the
 * verdict, as for any outside improver. With no editor agent of yours, the
 * first cloud agent leads the button; the others sit on the caret.
 */

/* What to tell the coding agent to pick up — the issue, the improvement or the run. */
const targetRef = (t) => t?.issueId || t?.optId || t?.runId || "this";

const SIZES = {
  small: { typography: "s3", height: 26, px: 1, icon: 14 },
  medium: { typography: "s2", height: 30, px: 1.25, icon: 15 },
};

export default function CopyBriefButton({
  getBrief,
  label = "Copy for coding agent",
  size = "medium",
  target,
  onOpened,
  disabledReason,
  sx,
}) {
  const navigate = useNavigate();
  const integrations = useIntegrations();
  const agent = connectedCodingAgent(integrations);
  const clouds = connectedCloudAgents(integrations);
  const [done, setDone] = useState(null); // null | "copied" | "sent" | "dispatched"
  const [cloud, setCloud] = useState(null); // the cloud agent last dispatched to
  const [menu, setMenu] = useState(null);
  /* Where the agent's attempts will land, when handing it over started an improvement. */
  const [landing, setLanding] = useState(null);
  const anchor = useRef(null);
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);

  const flash = (what, ms) => {
    setDone(what);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setDone(null), ms);
  };

  const copy = () => {
    try {
      navigator.clipboard?.writeText(getBrief());
    } catch {
      /* clipboard unavailable (sandbox / insecure origin) — no-op */
    }
    flash("copied", 1600);
  };

  const open = () => {
    handBriefTo({ agentId: agent.id, target, brief: getBrief() });
    if (target?.kind === "improvement") {
      startImproverSession({
        agentId: agent.id,
        envId: target.envId,
        optId: target.optId,
        where: "future-agi",
      });
    }
    /* An issue or a run: the caller starts the improvement its attempts land on. */
    const where = onOpened?.(agent) || null;
    setLanding(where);
    /* With somewhere to go, long enough to read it and click. */
    flash("sent", where ? 12000 : 4200);
  };

  const dispatch = (c) => {
    dispatchToCloudAgent({ agentId: c.id, target, brief: getBrief() });
    setCloud(c);
    const where = onOpened?.(c) || null;
    setLanding(where);
    flash("dispatched", where ? 12000 : 5200);
  };

  const s = SIZES[size] || SIZES.medium;
  const base = {
    typography: s.typography,
    fontWeight: 600,
    textTransform: "none",
    color: "text.primary",
    borderColor: "divider",
    height: s.height,
    flexShrink: 0,
    whiteSpace: "nowrap",
    "&:hover": {
      borderColor: "text.primary",
      bgcolor: "transparent",
      zIndex: 1,
    },
  };

  /* No code to change: a coding agent has nowhere to push its candidate. */
  if (disabledReason) {
    return (
      <Tooltip arrow title={disabledReason}>
        <span>
          <Button
            size="small"
            variant="outlined"
            disabled
            startIcon={<Iconify icon="solar:copy-linear" width={s.icon} />}
            sx={{ ...base, ...sx }}
          >
            {label}
          </Button>
        </span>
      </Tooltip>
    );
  }

  if (!agent && !clouds.length) {
    return (
      <Button
        size="small"
        variant="outlined"
        onClick={copy}
        startIcon={
          <Iconify
            icon={done ? "solar:check-circle-bold" : "solar:copy-linear"}
            width={s.icon}
          />
        }
        sx={{ ...base, ...sx }}
      >
        {done ? "Copied" : label}
      </Button>
    );
  }

  const markSize = Math.round(s.icon / 0.55);
  /* Your editor agent leads when you have one; otherwise a connected cloud
     agent does — one-click dispatch — and Copy moves to the caret. */
  const lead = !agent ? clouds[0] : null;
  const idle = agent
    ? {
        icon: <BrandMark brand={agent.id} size={markSize} tile={false} />,
        text: `Open in ${agent.label}`,
        onClick: open,
      }
    : {
        icon: <BrandMark brand={lead.id} size={markSize} tile={false} />,
        text: `Dispatch to ${lead.short || lead.label}`,
        onClick: () => dispatch(lead),
      };
  const doneText = {
    sent: agent && `Opened in ${agent.label}`,
    copied: "Copied",
    dispatched: cloud && `Dispatched to ${cloud.short || cloud.label}`,
  }[done];
  return (
    <>
      <Stack ref={anchor} direction="row" sx={{ flexShrink: 0, ...sx }}>
        <Button
          size="small"
          variant="outlined"
          onClick={idle.onClick}
          startIcon={
            done ? (
              <Iconify icon="solar:check-circle-bold" width={s.icon} />
            ) : (
              idle.icon
            )
          }
          sx={{ ...base, borderTopRightRadius: 0, borderBottomRightRadius: 0 }}
        >
          {doneText || idle.text}
        </Button>
        <Button
          size="small"
          variant="outlined"
          aria-label="More ways to hand off"
          onClick={(e) => setMenu(e.currentTarget)}
          sx={{
            ...base,
            minWidth: 0,
            ml: "-1px",
            borderTopLeftRadius: 0,
            borderBottomLeftRadius: 0,
            color: "text.secondary",
          }}
        >
          <Iconify icon="solar:alt-arrow-down-linear" width={s.icon + 1} />
        </Button>
      </Stack>

      <Menu
        anchorEl={menu}
        open={!!menu}
        onClose={() => setMenu(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      >
        <MenuItem
          onClick={() => {
            setMenu(null);
            copy();
          }}
          sx={{ typography: "s2" }}
        >
          <Iconify
            icon="solar:copy-linear"
            width={16}
            sx={{ mr: 1, color: "text.secondary" }}
          />
          Copy brief instead
        </MenuItem>
        {clouds
          .filter((c) => c.id !== lead?.id)
          .map((c) => (
            <MenuItem
              key={c.id}
              onClick={() => {
                setMenu(null);
                dispatch(c);
              }}
              sx={{ typography: "s2" }}
            >
              <Box sx={{ mr: 1, display: "inline-flex" }}>
                <BrandMark brand={c.id} size={27} tile={false} />
              </Box>
              {`Dispatch to ${c.label}`}
            </MenuItem>
          ))}
        <Divider sx={{ my: 0.5 }} />
        <MenuItem
          onClick={() => {
            setMenu(null);
            navigate(`${paths.dashboard.settings.integrations}/coding-agents`);
          }}
          sx={{ typography: "s2" }}
        >
          <Iconify icon="solar:settings-linear" width={16} sx={{ mr: 1 }} />
          IDE settings
        </MenuItem>
      </Menu>

      {/* Not a modal — the person keeps working; it fades with the sent state. */}
      <Popper
        open={done === "sent" || done === "dispatched"}
        anchorEl={anchor.current}
        placement="bottom-end"
        transition
        sx={{ zIndex: (t) => t.zIndex.modal + 1 }}
      >
        {({ TransitionProps }) => (
          <Fade {...TransitionProps} timeout={160}>
            <Box>
              <ClickAwayListener onClickAway={() => setDone(null)}>
                <Section
                  variant="panel"
                  sx={{ mt: 0.75, px: 1.5, py: 1, maxWidth: 300 }}
                >
                  {done === "dispatched" && cloud ? (
                    <>
                      <Typography
                        sx={{ typography: "s2", fontWeight: 600 }}
                      >{`Dispatched to ${cloud.label}`}</Typography>
                      <Typography
                        sx={{
                          typography: "s3",
                          color: "text.secondary",
                          mt: 0.25,
                        }}
                      >
                        It works in its own sandbox against the agent&apos;s
                        repository. The change comes back as a candidate for
                        Future AGI to check.
                      </Typography>
                    </>
                  ) : agent ? (
                    <>
                      <Typography
                        sx={{ typography: "s2", fontWeight: 600 }}
                      >{`Ready in ${agent.label}`}</Typography>
                      <Typography
                        sx={{
                          typography: "s3",
                          color: "text.secondary",
                          mt: 0.25,
                        }}
                      >
                        {`Ask it to "fix ${targetRef(target)}". It fetches the brief itself and submits each change for Future AGI to check.`}
                      </Typography>
                    </>
                  ) : null}
                  {/* Its attempts have a home: say where, one click away. */}
                  {landing?.href && (
                    <Button
                      size="small"
                      onClick={() => {
                        setDone(null);
                        navigate(
                          landing.href,
                          landing.state ? { state: landing.state } : undefined,
                        );
                      }}
                      endIcon={
                        <Iconify icon="solar:arrow-right-linear" width={14} />
                      }
                      sx={{ color: "text.primary", mt: 0.75, minWidth: 0 }}
                    >
                      {landing.label || "See its attempts"}
                    </Button>
                  )}
                </Section>
              </ClickAwayListener>
            </Box>
          </Fade>
        )}
      </Popper>
    </>
  );
}

CopyBriefButton.propTypes = {
  getBrief: PropTypes.func.isRequired,
  label: PropTypes.string,
  size: PropTypes.oneOf(["small", "medium"]),
  /* What the brief is about — { kind: "improvement", envId, optId } |
     { kind: "issue", issueId } | { kind: "run", envId, runId }. */
  target: PropTypes.shape({
    kind: PropTypes.oneOf(["improvement", "issue", "run"]).isRequired,
    envId: PropTypes.string,
    optId: PropTypes.string,
    issueId: PropTypes.string,
    runId: PropTypes.string,
  }),
  /* Called with the agent the brief went to. For an issue or a run it starts
     the improvement the agent's attempts land on, and may return
     { href, label, state } — where to watch them. */
  onOpened: PropTypes.func,
  /* Why the coding-agent route can't be used here — shown on a disabled button. */
  disabledReason: PropTypes.node,
  sx: PropTypes.object,
};
