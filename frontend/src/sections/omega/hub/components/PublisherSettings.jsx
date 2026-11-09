/* eslint-disable react-refresh/only-export-components */
import React, { useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { HANDLE_RE } from "../constants";
import {
  EMAIL_UNVERIFIED,
  useCreatePublisher,
  useRequestVerification,
  useUpdatePublisher,
  useUploadAvatar,
} from "../api/queries";

import EmailVerifyNotice from "./EmailVerifyNotice";
import { PublisherAvatar } from "./EntryCover";
import { HubSection } from "./HubStates";
import TierBadge from "./TierBadge";

export function CreatePublisher() {
  const [handle, setHandle] = useState("");
  const [displayName, setDisplayName] = useState("");
  const create = useCreatePublisher();
  const handleOk = HANDLE_RE.test(handle);
  return (
    <HubSection title="Create your publisher" testId="hub-create-publisher">
      <Typography typography="s2" color="text.secondary">
        Your entries are published as handle/name. A handle can never be reused
        once taken, and trust words (official, verified and the like) are
        refused.
      </Typography>
      <Box
        sx={{
          display: "grid",
          gap: 1.5,
          gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
        }}
      >
        <TextField
          size="small"
          label="Handle"
          value={handle}
          onChange={(e) => setHandle(e.target.value.toLowerCase())}
          error={Boolean(handle) && !handleOk}
          helperText="Lower case letters, digits and -, 2 to 39 characters"
          inputProps={{ "data-testid": "hub-publisher-handle" }}
        />
        <TextField
          size="small"
          label="Display name"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          inputProps={{
            maxLength: 60,
            "data-testid": "hub-publisher-display-name",
          }}
        />
      </Box>
      {create.isError &&
        (create.error?.code === EMAIL_UNVERIFIED ? (
          <EmailVerifyNotice action="create a publisher" />
        ) : (
          <Alert severity="error">{create.error.message}</Alert>
        ))}
      <Box>
        <Button
          variant="contained"
          size="small"
          disabled={!handleOk || !displayName.trim() || create.isPending}
          onClick={() =>
            create.mutate({
              handle,
              kind: "user",
              displayName: displayName.trim(),
            })
          }
          data-testid="hub-publisher-create"
        >
          Create publisher
        </Button>
      </Box>
    </HubSection>
  );
}

// One publisher I act for: profile, avatar and the verification request
// (SPEC §6.5). Tier changes are a maintainer's decision.
export function PublisherProfile({ publisher }) {
  const [form, setForm] = useState({
    displayName: publisher.displayName || "",
    bio: publisher.bio || "",
    website: publisher.website || "",
    githubLogin: publisher.githubLogin || "",
  });
  const [evidence, setEvidence] = useState("");
  const update = useUpdatePublisher(publisher.handle);
  const avatar = useUploadAvatar(publisher.handle);
  const verify = useRequestVerification(publisher.handle);
  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));
  return (
    <HubSection
      title={`@${publisher.handle}`}
      testId="hub-publisher-profile"
      action={
        <TierBadge tier={publisher.tier} verifiedAt={publisher.verifiedAt} />
      }
    >
      {publisher.suspended && (
        <Alert severity="error">
          This publisher is suspended. New submissions are refused.
        </Alert>
      )}
      <Box sx={{ display: "flex", gap: 1.5, alignItems: "center" }}>
        <PublisherAvatar publisher={publisher} size={44} />
        <Button
          component="label"
          size="small"
          variant="outlined"
          disabled={avatar.isPending}
        >
          Upload an avatar
          <input
            hidden
            type="file"
            accept="image/png,image/jpeg"
            onChange={(e) =>
              e.target.files?.[0] && avatar.mutate(e.target.files[0])
            }
          />
        </Button>
        <Typography typography="s3" color="text.tertiary">
          PNG or JPEG, at most 256 KB
        </Typography>
      </Box>
      {avatar.isError && <Alert severity="error">{avatar.error.message}</Alert>}
      <Box
        sx={{
          display: "grid",
          gap: 1.5,
          gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
        }}
      >
        <TextField
          size="small"
          label="Display name"
          value={form.displayName}
          onChange={set("displayName")}
          inputProps={{ maxLength: 60 }}
        />
        <TextField
          size="small"
          label="Website (https)"
          value={form.website}
          onChange={set("website")}
        />
        <TextField
          size="small"
          label="GitHub login"
          value={form.githubLogin}
          onChange={set("githubLogin")}
          helperText="Shown as a link, never as proof"
        />
        <TextField
          size="small"
          label="Bio"
          value={form.bio}
          onChange={set("bio")}
          inputProps={{ maxLength: 160 }}
          helperText={`${form.bio.length} / 160`}
        />
      </Box>
      {update.isError && <Alert severity="error">{update.error.message}</Alert>}
      {update.isSuccess && <Alert severity="success">Saved.</Alert>}
      <Box>
        <Button
          size="small"
          variant="contained"
          disabled={update.isPending}
          onClick={() => update.mutate(form)}
        >
          Save profile
        </Button>
      </Box>
      {publisher.tier === "community" && (
        <Box
          sx={{
            borderTop: "1px solid",
            borderColor: "divider",
            pt: 1.5,
            display: "flex",
            flexDirection: "column",
            gap: 1,
          }}
        >
          <Typography typography="s2" fontWeight={600}>
            Verified publisher
          </Typography>
          {publisher.verificationRequested ? (
            <Typography typography="s2" color="text.secondary">
              Requested. A maintainer will verify or decline it.
            </Typography>
          ) : (
            <>
              <Typography typography="s2" color="text.secondary">
                Show who you are: an email on your company domain, or a page on
                your site that names this handle.
              </Typography>
              <TextField
                size="small"
                label="Evidence"
                value={evidence}
                onChange={(e) => setEvidence(e.target.value)}
                multiline
                minRows={2}
                inputProps={{ maxLength: 1000 }}
              />
              {verify.isError && (
                <Alert severity="error">{verify.error.message}</Alert>
              )}
              <Box>
                <Button
                  size="small"
                  variant="outlined"
                  disabled={!evidence.trim() || verify.isPending}
                  onClick={() => verify.mutate(evidence.trim())}
                >
                  Request verification
                </Button>
              </Box>
            </>
          )}
        </Box>
      )}
    </HubSection>
  );
}

PublisherProfile.propTypes = { publisher: PropTypes.object.isRequired };
