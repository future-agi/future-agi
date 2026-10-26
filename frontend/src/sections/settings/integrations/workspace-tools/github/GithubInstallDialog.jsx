import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import {
  Box,
  Button,
  CircularProgress,
  Dialog,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  accountOf,
  getIntegrations,
  installGithub,
  installationFor,
  markScanned,
  reposOfInstall,
  useIntegrations,
} from "../store";
import BrandMark from "../BrandMark";
import { Returning, WaitingForWindow } from "../ExternalPage";
import { CONNECT_PATHS, useConnectWindow } from "../connectWindow";

const MONO = "ui-monospace, Menlo, monospace";

/**
 * Future AGI's side of installing the GitHub App. The install itself happens
 * on GitHub, in its own window (see windows/GithubInstallWindow); this waits
 * for it and then shows what GitHub sent back:
 *   install  → connected, and we scan the repositories one by one
 *   update   → repository access changed (Configure)
 *   cancel / window closed → nothing changed
 *
 * `configure` opens an existing installation's settings on GitHub.
 */
export default function GithubInstallDialog({
  open,
  onClose,
  onInstalled,
  configure,
}) {
  useIntegrations(); // re-render as installs change
  const [phase, setPhase] = useState("waiting"); // waiting → returning → outcome
  const [result, setResult] = useState(null); // { outcome, account, access, repos }
  const [scanned, setScanned] = useState(0);
  /* Repositories to read for agents: all of them on a new install, only the
     newly added ones when access changed. */
  const [toScan, setToScan] = useState([]);

  const win = useConnectWindow((msg) => {
    if (msg.outcome === "closed" || msg.outcome === "cancel") {
      setResult({ outcome: msg.outcome === "closed" ? "closed" : "cancel" });
      setPhase("outcome");
      return;
    }
    setResult(msg);
    setPhase("returning");
  });
  const url = `${CONNECT_PATHS.github}${configure ? `?configure=${encodeURIComponent(configure)}` : ""}`;

  useEffect(() => {
    if (!open) return;
    setPhase("waiting");
    setResult(null);
    setScanned(0);
    setToScan([]);
    win.open(url);
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  /* GitHub redirected back — verify, then record what it said. */
  useEffect(() => {
    if (phase !== "returning" || !result) return undefined;
    const t = setTimeout(() => {
      const { outcome, account, access, repos } = result;
      const before = getIntegrations().scans || {};
      installGithub({ account, access, repos });
      const all = reposOfInstall(installationFor(account));
      setToScan(
        outcome === "install" ? all : all.filter((r) => !before[r.name]),
      );
      setScanned(0);
      setPhase("outcome");
    }, 900);
    return () => clearTimeout(t);
  }, [phase, result]);

  const account = result?.account;
  const meta = account ? accountOf(account) : null;
  const accountName =
    meta?.kind === "Personal account" ? "your personal account" : account;
  const inst = account ? installationFor(account) : null;
  const covered = inst ? reposOfInstall(inst) : [];

  /* Scanning reads each file tree, so results arrive one repository at a time. */
  useEffect(() => {
    if (phase !== "outcome" || scanned >= toScan.length) return undefined;
    const t = setTimeout(() => {
      markScanned(toScan[scanned].name);
      setScanned((n) => n + 1);
    }, 750);
    return () => clearTimeout(t);
  }, [phase, scanned, toScan]);

  const done =
    phase === "outcome" &&
    (result?.outcome === "install" || result?.outcome === "update");
  const close = () => {
    if (phase === "waiting") win.close();
    if (done) onInstalled?.();
    onClose();
  };
  const retry = () => {
    setPhase("waiting");
    setResult(null);
    setScanned(0);
    setToScan([]);
    win.open(url);
  };
  const agentsFound = toScan
    .slice(0, scanned)
    .reduce((n, r) => n + r.agents.length, 0);

  return (
    <Dialog
      open={open}
      onClose={close}
      PaperProps={{
        sx: {
          borderRadius: 2,
          maxWidth: 560,
          width: "100%",
          bgcolor: "background.paper",
          backgroundImage: "none",
          border: "1px solid",
          borderColor: "divider",
        },
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ px: 3, pt: 2.5, pb: 1.5 }}
      >
        <BrandMark brand="github" size={36} />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "m3", fontWeight: 700 }}>
            {configure
              ? `Repository access · ${configure}`
              : "Install the GitHub App"}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {configure
              ? "Repository access is changed on GitHub."
              : "You choose the account and repositories on GitHub."}
          </Typography>
        </Box>
        <IconButton size="small" onClick={close}>
          <Iconify icon="eva:close-fill" width={18} />
        </IconButton>
      </Stack>

      <Box sx={{ px: 3, pb: 2.5, maxHeight: "64vh", overflowY: "auto" }}>
        {phase === "waiting" && (
          <WaitingForWindow
            site="GitHub"
            status={win.status}
            onFocus={win.focus}
            onOpen={
              <Button
                size="small"
                variant="contained"
                color="primary"
                onClick={() => win.open(url)}
                startIcon={<Iconify icon="mdi:github" width={15} />}
              >
                Open GitHub
              </Button>
            }
          >
            {configure
              ? "Tick or untick repositories there and save. This updates as soon as you're done."
              : "Pick the account and repositories there, then install and authorize. This updates as soon as you're done."}
          </WaitingForWindow>
        )}

        {phase === "returning" && (
          <Returning
            label={
              result?.outcome === "update"
                ? "Updating repository access…"
                : `Linking ${account} to your workspace…`
            }
          />
        )}

        {phase === "outcome" && result?.outcome === "install" && (
          <Stack spacing={2}>
            <Result
              icon="solar:check-circle-bold"
              color="#16A34A"
              title={`Installed on ${accountName}`}
              meta={`${inst?.access === "all" ? "All repositories" : `${covered.length} repositor${covered.length === 1 ? "y" : "ies"}`} · read only`}
            />
            <Typography
              sx={{ typography: "s3", color: "text.secondary", mt: -1 }}
            >
              We only read your code. We&apos;ll ask for write access when you
              open your first draft pull request.
            </Typography>
            <Box>
              <ScanList
                toScan={toScan}
                scanned={scanned}
                agentsFound={agentsFound}
              />
            </Box>
          </Stack>
        )}

        {phase === "outcome" && result?.outcome === "update" && (
          <Stack spacing={2}>
            <Result
              icon="solar:check-circle-bold"
              color="#16A34A"
              title={`Repository access updated · ${account}`}
              meta={
                inst?.access === "all"
                  ? "All repositories"
                  : `${covered.length} repositor${covered.length === 1 ? "y" : "ies"}`
              }
            />
            {toScan.length > 0 && (
              <Box>
                <ScanList
                  toScan={toScan}
                  scanned={scanned}
                  agentsFound={agentsFound}
                  added
                />
              </Box>
            )}
          </Stack>
        )}

        {phase === "outcome" &&
          (result?.outcome === "cancel" || result?.outcome === "closed") && (
            <Stack spacing={1.5}>
              <Result
                icon="solar:close-circle-linear"
                color="text.secondary"
                title="Nothing changed"
                meta={
                  result.outcome === "closed"
                    ? "GitHub window closed"
                    : "Cancelled on GitHub"
                }
              />
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                {configure
                  ? "Repository access is the same as before."
                  : "The app wasn't installed, so nothing was connected."}
              </Typography>
            </Stack>
          )}
      </Box>

      {phase !== "returning" && (
        <Stack
          direction="row"
          justifyContent="flex-end"
          spacing={1}
          sx={{
            px: 3,
            py: 1.75,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          {phase === "waiting" && (
            <Button
              size="small"
              onClick={close}
              sx={{ color: "text.secondary" }}
            >
              Cancel
            </Button>
          )}
          {phase === "outcome" &&
            (result?.outcome === "cancel" || result?.outcome === "closed") && (
              <>
                <Button
                  size="small"
                  onClick={close}
                  sx={{ color: "text.secondary" }}
                >
                  Close
                </Button>
                <Button
                  size="small"
                  variant="contained"
                  color="primary"
                  onClick={retry}
                >
                  Try again
                </Button>
              </>
            )}
          {phase === "outcome" &&
            result?.outcome !== "cancel" &&
            result?.outcome !== "closed" && (
              <Button
                size="small"
                variant="contained"
                color="primary"
                onClick={close}
              >
                Done
              </Button>
            )}
        </Stack>
      )}
    </Dialog>
  );
}

GithubInstallDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onInstalled: PropTypes.func,
  configure: PropTypes.string,
};

/* Repositories being read for agents, one at a time. */
function ScanList({ toScan, scanned, agentsFound, added }) {
  const busy = scanned < toScan.length;
  return (
    <>
      <Label>
        {busy
          ? `Scanning ${added ? "new repositories" : "for agents"} · ${scanned} of ${toScan.length}`
          : `Agents found${added ? " in new repositories" : ""} · ${agentsFound}`}
      </Label>
      <Box
        sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}
      >
        {toScan.map((r, i) => {
          const isDone = i < scanned;
          return (
            <Stack
              key={r.name}
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{
                px: 1.5,
                py: 0.875,
                borderTop: i ? "1px solid" : "none",
                borderColor: "divider",
              }}
            >
              {isDone ? (
                <Iconify
                  icon={
                    r.agents.length
                      ? "solar:check-circle-linear"
                      : "solar:minus-circle-linear"
                  }
                  width={14}
                  sx={{
                    color: r.agents.length ? "text.primary" : "text.disabled",
                    flexShrink: 0,
                  }}
                />
              ) : (
                <CircularProgress
                  size={12}
                  thickness={6}
                  sx={{
                    color: "text.disabled",
                    flexShrink: 0,
                    visibility: i === scanned ? "visible" : "hidden",
                  }}
                />
              )}
              <Typography
                noWrap
                sx={{
                  fontFamily: MONO,
                  fontSize: 12.5,
                  flex: 1,
                  minWidth: 0,
                  color: isDone ? "text.primary" : "text.subtitle",
                }}
              >
                {r.name}
              </Typography>
              <Typography
                noWrap
                sx={{
                  typography: "s3",
                  color: "text.secondary",
                  maxWidth: 240,
                }}
              >
                {!isDone
                  ? i === scanned
                    ? "Scanning…"
                    : "Waiting"
                  : r.agents.length
                    ? r.agents.map((a) => a.name).join(", ")
                    : "No agents found"}
              </Typography>
            </Stack>
          );
        })}
      </Box>
      <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.75 }}>
        {busy
          ? "You can close this. Scanning carries on in the background."
          : "Connect one from the GitHub settings page, or from Agents → Connect agent."}
      </Typography>
    </>
  );
}
ScanList.propTypes = {
  toScan: PropTypes.array,
  scanned: PropTypes.number,
  agentsFound: PropTypes.number,
  added: PropTypes.bool,
};

function Result({ icon, color, title, meta }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{
        px: 1.5,
        py: 1,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <Iconify icon={icon} width={16} sx={{ color, flexShrink: 0 }} />
      <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1 }}>
        {title}
      </Typography>
      {meta && (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {meta}
        </Typography>
      )}
    </Stack>
  );
}
Result.propTypes = {
  icon: PropTypes.string,
  color: PropTypes.string,
  title: PropTypes.string,
  meta: PropTypes.string,
};

function Label({ children }) {
  return (
    <Typography
      sx={{
        typography: "s3",
        fontWeight: 700,
        textTransform: "uppercase",
        letterSpacing: 0.5,
        color: "text.subtitle",
        mb: 0.75,
      }}
    >
      {children}
    </Typography>
  );
}
Label.propTypes = { children: PropTypes.node };
