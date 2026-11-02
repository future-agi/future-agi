import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { Helmet } from "react-helmet-async";
import { useSearchParams } from "react-router-dom";
import {
  Box,
  Button,
  IconButton,
  InputAdornment,
  Radio,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  GITHUB_ACCOUNTS,
  GITHUB_PERMISSIONS,
  accountOf,
  installationFor,
  reposOfAccount,
} from "../store";
import BrandMark from "../BrandMark";
import StandInWindow, { HandoffSteps } from "../ExternalPage";
import { replyToOpener } from "../connectWindow";
import { clickable, focusRing } from "../clickable";

const MONO = "ui-monospace, Menlo, monospace";

/**
 * Stand-in for GitHub's install page, opened in its own window by "Install
 * GitHub App". Everything here is GitHub's: which account, which
 * repositories (nothing pre-picked — GitHub doesn't know where agents are),
 * the permissions, and the Authorize step. Finishing reports back to the
 * Future AGI window, the way GitHub redirects to our setup URL.
 *
 * ?configure=<account> opens an existing installation's settings instead.
 */
export default function GithubInstallWindow() {
  const [params] = useSearchParams();
  const configure = params.get("configure");
  const [step, setStep] = useState(configure ? "repos" : "account"); // account → repos → authorize → sent
  const [account, setAccount] = useState(configure || null);
  const [access, setAccess] = useState("selected");
  const [repos, setRepos] = useState([]);
  const [sent, setSent] = useState(null);

  /* Configuring shows what the installation has today. */
  useEffect(() => {
    if (!configure) return;
    const inst = installationFor(configure);
    setAccess(inst?.access || "selected");
    setRepos(inst?.repos || []);
  }, [configure]);

  const meta = account ? accountOf(account) : null;
  const existing = account ? installationFor(account) : null;
  const accountName =
    meta?.kind === "Personal account" ? "your personal account" : account;
  const ready = access === "all" || repos.length > 0;

  const pick = (login) => {
    const inst = installationFor(login);
    setAccount(login);
    setAccess(inst?.access || "selected");
    setRepos(inst?.repos || []);
    setStep("repos");
  };

  const finish = (outcome) => {
    const payload = { provider: "github", outcome, account, access, repos };
    if (!replyToOpener(payload)) setSent(outcome); // opened directly, not from Future AGI
  };
  const cancel = () => {
    if (!replyToOpener({ provider: "github", outcome: "cancel" }))
      setSent("cancel");
  };

  const url =
    step === "authorize"
      ? "github.com/login/oauth/authorize?client_id=future-agi"
      : configure || existing
        ? meta?.kind === "Organization"
          ? `github.com/organizations/${account}/settings/installations`
          : "github.com/settings/installations"
        : step === "repos"
          ? "github.com/apps/future-agi/installations/new/permissions"
          : "github.com/apps/future-agi/installations/new";
  /* Every account installs straight away — no owner approval in between. */
  const steps = ["Account", "Repositories", "Authorize"];

  if (sent) {
    return (
      <StandInWindow site="GitHub" url="github.com">
        <Typography sx={{ typography: "s1", fontWeight: 700 }}>
          {sent === "cancel" ? "Cancelled" : "Done"}
        </Typography>
        <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.5 }}>
          You can close this window and go back to Future AGI.
        </Typography>
      </StandInWindow>
    );
  }

  return (
    <StandInWindow
      site="GitHub"
      url={url}
      footer={
        <>
          {step === "account" && (
            <Button size="small" onClick={cancel} sx={btnQuiet}>
              Cancel
            </Button>
          )}
          {step === "repos" && (
            <>
              <Button
                size="small"
                onClick={() => (configure ? cancel() : setStep("account"))}
                sx={btnQuiet}
              >
                {configure ? "Cancel" : "Back"}
              </Button>
              <Button
                size="small"
                variant="contained"
                color="primary"
                disabled={!ready}
                onClick={() =>
                  existing ? finish("update") : setStep("authorize")
                }
              >
                {existing ? "Save" : "Install"}
              </Button>
            </>
          )}
          {step === "authorize" && (
            <>
              <Button size="small" onClick={cancel} sx={btnQuiet}>
                Cancel
              </Button>
              <Button
                size="small"
                variant="contained"
                color="primary"
                onClick={() => finish("install")}
              >
                Authorize Future AGI
              </Button>
            </>
          )}
        </>
      }
    >
      <Helmet>
        <title>Install Future AGI · GitHub (prototype stand-in)</title>
      </Helmet>
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 2.5 }}>
        <BrandMark brand="github" size={32} />
        <Typography sx={{ typography: "m3", fontWeight: 700 }}>
          {step === "authorize"
            ? "Authorize Future AGI"
            : configure || existing
              ? "Configure Future AGI"
              : step === "repos"
                ? `Install Future AGI on ${accountName}`
                : "Install Future AGI"}
        </Typography>
      </Stack>
      {!configure && (
        <HandoffSteps
          steps={steps}
          current={{ account: 0, repos: 1, authorize: 2 }[step]}
          sx={{ mb: 2.5 }}
        />
      )}

      {step === "account" && (
        <>
          <Typography
            sx={{ typography: "s2", color: "text.secondary", mb: 1.5 }}
          >
            Where do you want to install Future AGI?
          </Typography>
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
            }}
          >
            {GITHUB_ACCOUNTS.map((a, i) => {
              const n = reposOfAccount(a.login).length;
              return (
                <Stack
                  key={a.login}
                  direction="row"
                  alignItems="center"
                  spacing={1.25}
                  sx={{
                    px: 1.5,
                    py: 1.25,
                    borderTop: i ? "1px solid" : "none",
                    borderColor: "divider",
                  }}
                >
                  <Avatar login={a.login} org={a.kind === "Organization"} />
                  <Box flex={1} minWidth={0}>
                    <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                      {a.login}
                    </Typography>
                    <Typography
                      sx={{ typography: "s3", color: "text.subtitle" }}
                    >
                      {[a.kind, n ? `${n} repositories` : "no repositories"]
                        .filter(Boolean)
                        .join(" · ")}
                    </Typography>
                  </Box>
                  <Button
                    size="small"
                    variant="outlined"
                    onClick={() => pick(a.login)}
                    sx={btnOutline}
                  >
                    {installationFor(a.login) ? "Configure" : "Install"}
                  </Button>
                </Stack>
              );
            })}
          </Box>
        </>
      )}

      {step === "repos" && (
        <>
          <Typography
            sx={{ typography: "s3", color: "text.subtitle", mb: 1.5 }}
          >{`${meta?.kind} · ${account}`}</Typography>
          <Label>Repository access</Label>
          <Stack spacing={0.75}>
            {[
              {
                id: "all",
                title: "All repositories",
                desc: "Includes repositories created later.",
              },
              {
                id: "selected",
                title: "Only select repositories",
                desc: "Pick at least one.",
              },
            ].map((o) => (
              <Stack
                key={o.id}
                direction="row"
                alignItems="flex-start"
                spacing={1}
                onClick={() => setAccess(o.id)}
                sx={{
                  p: 1,
                  borderRadius: 1,
                  cursor: "pointer",
                  border: "1px solid",
                  borderColor: access === o.id ? "text.primary" : "divider",
                }}
              >
                <Radio
                  size="small"
                  checked={access === o.id}
                  sx={{ p: 0.25 }}
                />
                <Box>
                  <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                    {o.title}
                  </Typography>
                  <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                    {o.desc}
                  </Typography>
                </Box>
              </Stack>
            ))}
          </Stack>
          {access === "selected" && (
            <RepoPicker account={account} value={repos} onChange={setRepos} />
          )}

          {!existing && (
            <Box sx={{ mt: 2.5 }}>
              <Label>With these permissions</Label>
              <Typography
                sx={{ typography: "s3", color: "text.secondary", mb: 1 }}
              >
                We only read your code. We&apos;ll ask for write access when you
                open your first draft pull request.
              </Typography>
              <Stack spacing={0.75}>
                {GITHUB_PERMISSIONS.map((p) => (
                  <Stack
                    key={p.scope}
                    direction="row"
                    spacing={1}
                    alignItems="flex-start"
                  >
                    <Iconify
                      icon="mdi:check"
                      width={14}
                      sx={{ color: "text.secondary", mt: 0.25, flexShrink: 0 }}
                    />
                    <Typography
                      sx={{ typography: "s3", color: "text.secondary" }}
                    >
                      <Box
                        component="span"
                        sx={{ fontWeight: 700, color: "text.primary" }}
                      >{`${p.scope} · ${p.access}`}</Box>
                      {`: ${p.why}`}
                    </Typography>
                  </Stack>
                ))}
              </Stack>
            </Box>
          )}
        </>
      )}

      {step === "authorize" && (
        <>
          <Typography sx={{ typography: "s2", color: "text.secondary", mb: 2 }}>
            Future AGI wants to confirm who you are, so it links this
            installation to your workspace and no one else&apos;s.
          </Typography>
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
            }}
          >
            {[
              [
                "solar:user-id-linear",
                "Verify your GitHub identity",
                "Read-only: your username and public profile",
              ],
              [
                "solar:key-minimalistic-square-linear",
                "Know which installations you can access",
                `Confirms you can see the ${account} installation`,
              ],
            ].map(([icon, title, sub], i) => (
              <Stack
                key={title}
                direction="row"
                spacing={1.25}
                alignItems="center"
                sx={{
                  px: 1.5,
                  py: 1.25,
                  borderTop: i ? "1px solid" : "none",
                  borderColor: "divider",
                }}
              >
                <Iconify
                  icon={icon}
                  width={16}
                  sx={{ color: "text.secondary", flexShrink: 0 }}
                />
                <Box>
                  <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                    {title}
                  </Typography>
                  <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                    {sub}
                  </Typography>
                </Box>
              </Stack>
            ))}
          </Box>
        </>
      )}
    </StandInWindow>
  );
}

/* GitHub's picker: search the account's repositories and add them one by one. */
function RepoPicker({ account, value, onChange }) {
  const [q, setQ] = useState("");
  const all = reposOfAccount(account);
  const matches = useMemo(
    () =>
      all.filter(
        (r) =>
          !value.includes(r.name) &&
          r.name.toLowerCase().includes(q.trim().toLowerCase()),
      ),
    [all, value, q],
  );
  if (!all.length)
    return (
      <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 1 }}>
        No repositories on this account yet.
      </Typography>
    );
  return (
    <Box sx={{ mt: 1.25 }}>
      <TextField
        size="small"
        fullWidth
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Search for a repository"
        InputProps={{
          sx: { typography: "s2" },
          startAdornment: (
            <InputAdornment position="start">
              <Iconify icon="solar:magnifer-linear" width={15} />
            </InputAdornment>
          ),
        }}
      />
      {(q || !value.length) && matches.length > 0 && (
        <Box
          sx={{
            mt: 0.75,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
          }}
        >
          {matches.map((r, i) => (
            <Stack
              key={r.name}
              direction="row"
              alignItems="center"
              spacing={1}
              {...clickable(() => {
                onChange([...value, r.name]);
                setQ("");
              })}
              sx={{
                px: 1.25,
                py: 0.75,
                cursor: "pointer",
                borderTop: i ? "1px solid" : "none",
                borderColor: "divider",
                "&:hover": { bgcolor: "action.hover" },
                ...focusRing,
              }}
            >
              <Iconify
                icon={
                  r.private
                    ? "solar:lock-keyhole-minimalistic-linear"
                    : "solar:folder-linear"
                }
                width={14}
                sx={{ color: "text.subtitle" }}
              />
              <Typography sx={{ fontFamily: MONO, fontSize: 12.5, flex: 1 }}>
                {r.name}
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                {r.private ? "Private" : "Public"}
              </Typography>
            </Stack>
          ))}
        </Box>
      )}
      {value.length > 0 && (
        <Box sx={{ mt: 1.25 }}>
          <Typography
            sx={{ typography: "s3", color: "text.subtitle", mb: 0.5 }}
          >{`Selected ${value.length} repositor${value.length === 1 ? "y" : "ies"}`}</Typography>
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
            }}
          >
            {value.map((name, i) => (
              <Stack
                key={name}
                direction="row"
                alignItems="center"
                spacing={1}
                sx={{
                  pl: 1.25,
                  pr: 0.5,
                  py: 0.25,
                  borderTop: i ? "1px solid" : "none",
                  borderColor: "divider",
                }}
              >
                <Typography sx={{ fontFamily: MONO, fontSize: 12.5, flex: 1 }}>
                  {name}
                </Typography>
                <IconButton
                  size="small"
                  onClick={() => onChange(value.filter((v) => v !== name))}
                >
                  <Iconify icon="eva:close-fill" width={15} />
                </IconButton>
              </Stack>
            ))}
          </Box>
        </Box>
      )}
    </Box>
  );
}
RepoPicker.propTypes = {
  account: PropTypes.string,
  value: PropTypes.array,
  onChange: PropTypes.func,
};

function Avatar({ login, org }) {
  return (
    <Box
      sx={{
        width: 28,
        height: 28,
        borderRadius: org ? 0.75 : "50%",
        display: "grid",
        placeItems: "center",
        bgcolor: "background.neutral",
        flexShrink: 0,
      }}
    >
      <Typography sx={{ typography: "s3", fontWeight: 700 }}>
        {login[0].toUpperCase()}
      </Typography>
    </Box>
  );
}
Avatar.propTypes = { login: PropTypes.string, org: PropTypes.bool };

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

const btnQuiet = { typography: "s2", fontWeight: 700, color: "text.secondary" };
const btnOutline = {
  typography: "s3",
  fontWeight: 700,
  height: 28,
  color: "text.primary",
  borderColor: "divider",
};
