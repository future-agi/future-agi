/* eslint-disable react-refresh/only-export-components */
import React, { useEffect, useId, useRef, useState } from "react";
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
import TierBadge from "./TierBadge";

// What failed and how to fix it, in the server's own words. A request that
// got no answer (dropped, timed out or cancelled) has no HTTP status and no
// server code, and a gateway error with no reason reads only "Request failed
// (502)"; for both, the caller's own sentence says what failed instead.
const NO_ANSWER = "The Hub registry did not answer; try again in a moment.";

export const problemText = (error, fallback) => {
  const answered = Boolean(error?.status || error?.code);
  const message =
    answered && error?.message && !/^Request failed/.test(error.message)
      ? error.message
      : null;
  return [message, error?.fix].filter(Boolean).join(" ") || fallback || "";
};

// Refusals that belong to one field, shown on that field.
const HANDLE_CODES = new Set([
  "OMEGA_HUB_HANDLE_INVALID",
  "OMEGA_HUB_HANDLE_RESERVED",
  "OMEGA_HUB_HANDLE_TAKEN",
]);
const NAME_CODES = new Set(["OMEGA_HUB_DISPLAY_NAME_RESERVED"]);

const formOf = (publisher) => ({
  displayName: publisher.displayName || "",
  bio: publisher.bio || "",
  website: publisher.website || "",
  githubLogin: publisher.githubLogin || "",
});

// The form for a new user publisher. Settings opens it at once for a person
// with no publisher, where the section's own title and line introduce it
// (titled false), and behind "Create your own publisher" for anyone who
// already acts for one, with its own heading below a hairline.
export function CreatePublisher({
  id,
  autoFocus = false,
  divided = false,
  titled = true,
  onCancel,
}) {
  const [handle, setHandle] = useState("");
  const [displayName, setDisplayName] = useState("");
  const create = useCreatePublisher();
  const fields = useId();
  const handleOk = HANDLE_RE.test(handle);
  const code = create.isError ? create.error?.code : null;
  const handleError = HANDLE_CODES.has(code) ? problemText(create.error) : "";
  const nameError = NAME_CODES.has(code) ? problemText(create.error) : "";
  const otherError =
    create.isError && !handleError && !nameError && code !== EMAIL_UNVERIFIED;
  // After a success the form waits for /me to list the new publisher.
  const ready =
    handleOk &&
    Boolean(displayName.trim()) &&
    !create.isPending &&
    !create.isSuccess;
  const clearError = () => create.isError && create.reset();
  const submit = (event) => {
    event.preventDefault();
    if (!ready) return;
    create.mutate({ handle, kind: "user", displayName: displayName.trim() });
  };
  return (
    <Box
      component="form"
      id={id}
      noValidate
      onSubmit={submit}
      aria-labelledby={titled ? `${fields}-title` : undefined}
      aria-label={titled ? undefined : "New publisher"}
      data-testid="hub-create-publisher"
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
        ...(divided && {
          borderTop: "1px solid",
          borderColor: "divider",
          pt: 2,
        }),
      }}
    >
      {titled && (
        <Box>
          <Typography
            id={`${fields}-title`}
            component="h3"
            typography="s1"
            fontWeight="fontWeightSemiBold"
            color="text.primary"
          >
            New publisher
          </Typography>
          <Typography typography="s1" color="text.secondary" sx={{ mt: 0.25 }}>
            Your entries are published as handle/name. A handle can never be
            reused once taken, and trust words (official, verified and the like)
            are refused.
          </Typography>
        </Box>
      )}
      <Box
        sx={{
          display: "grid",
          gap: 1.5,
          alignItems: "start",
          gridTemplateColumns: {
            xs: "minmax(0, 1fr)",
            sm: "repeat(2, minmax(0, 1fr))",
          },
        }}
      >
        <TextField
          id={`${fields}-handle`}
          size="small"
          label="Handle"
          value={handle}
          autoFocus={autoFocus}
          onChange={(e) => {
            clearError();
            setHandle(e.target.value.toLowerCase());
          }}
          error={Boolean(handleError) || (Boolean(handle) && !handleOk)}
          helperText={
            handleError ||
            (handle && !handleOk
              ? "Use 2 to 39 lower case letters, digits and -, starting with a letter."
              : "Lower case letters, digits and -, 2 to 39 characters")
          }
          inputProps={{
            maxLength: 39,
            autoComplete: "off",
            spellCheck: false,
            "data-testid": "hub-publisher-handle",
          }}
        />
        <TextField
          id={`${fields}-name`}
          size="small"
          label="Display name"
          value={displayName}
          onChange={(e) => {
            clearError();
            setDisplayName(e.target.value);
          }}
          error={Boolean(nameError)}
          helperText={
            nameError || "Shown on your entries. At most 60 characters."
          }
          inputProps={{
            maxLength: 60,
            "data-testid": "hub-publisher-display-name",
          }}
        />
      </Box>
      {code === EMAIL_UNVERIFIED && (
        <EmailVerifyNotice action="create a publisher" />
      )}
      {otherError && (
        <Alert severity="error">
          {problemText(
            create.error,
            `The publisher was not created. ${NO_ANSWER}`,
          )}
        </Alert>
      )}
      <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
        <Button
          type="submit"
          variant="contained"
          size="small"
          disabled={!ready}
          data-testid="hub-publisher-create"
        >
          Create publisher
        </Button>
        {onCancel && (
          <Button size="small" color="inherit" onClick={onCancel}>
            Cancel
          </Button>
        )}
      </Box>
    </Box>
  );
}

CreatePublisher.propTypes = {
  id: PropTypes.string,
  autoFocus: PropTypes.bool,
  divided: PropTypes.bool,
  titled: PropTypes.bool,
  onCancel: PropTypes.func,
};

// One publisher I act for (SPEC §6.5): who it is, as text, with Edit profile
// opening the form in place; the avatar; and the verification request, which
// stays in view. Tier changes are a maintainer's decision. Profiles sit flat
// in their section, a hairline between one and the next.
export function PublisherProfile({ publisher }) {
  const [editing, setEditing] = useState(false);
  const [saved, setSaved] = useState(false);
  const [form, setForm] = useState(() => formOf(publisher));
  const [evidence, setEvidence] = useState("");
  const update = useUpdatePublisher(publisher.handle);
  const avatar = useUploadAvatar(publisher.handle);
  const verify = useRequestVerification(publisher.handle);
  const ids = useId();
  const formId = `${ids}-form`;
  const editButton = useRef(null);
  const firstField = useRef(null);
  const avatarInput = useRef(null);
  useEffect(() => {
    if (editing) firstField.current?.focus();
  }, [editing]);
  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));
  const open = () => {
    update.reset();
    setSaved(false);
    setForm(formOf(publisher));
    setEditing(true);
  };
  const close = () => {
    setEditing(false);
    editButton.current?.focus();
  };
  const save = async (event) => {
    event.preventDefault();
    if (update.isPending) return;
    try {
      await update.mutateAsync(form);
      setSaved(true);
      close();
    } catch {
      // The form stays open with the server's message and its fix.
    }
  };
  const details = [
    publisher.website,
    publisher.githubLogin ? `GitHub ${publisher.githubLogin}` : null,
  ].filter(Boolean);
  return (
    <Box
      data-testid="hub-publisher-profile"
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 2,
        minWidth: 0,
        "& + &": { borderTop: "1px solid", borderColor: "divider", pt: 2 },
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "flex-start",
          flexWrap: "wrap",
          gap: 1.5,
        }}
      >
        <PublisherAvatar publisher={publisher} size={40} />
        <Box sx={{ flex: "1 1 200px", minWidth: 0 }}>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              flexWrap: "wrap",
              columnGap: 1,
              rowGap: 0.5,
            }}
          >
            <Typography
              component="h3"
              typography="m3"
              fontWeight="fontWeightSemiBold"
              color="text.primary"
              sx={{ overflowWrap: "anywhere" }}
            >
              {publisher.displayName || `@${publisher.handle}`}
            </Typography>
            {publisher.displayName && (
              <Typography
                component="span"
                typography="s1"
                color="text.secondary"
                sx={{ fontFamily: "monospace" }}
              >
                {`@${publisher.handle}`}
              </Typography>
            )}
            <TierBadge
              tier={publisher.tier}
              verifiedAt={publisher.verifiedAt}
            />
          </Box>
          {publisher.bio && (
            <Typography typography="s1" color="text.secondary" sx={{ mt: 0.5 }}>
              {publisher.bio}
            </Typography>
          )}
          {details.length > 0 && (
            <Typography
              typography="s2"
              color="text.secondary"
              sx={{ mt: 0.5, overflowWrap: "anywhere" }}
            >
              {details.join(" · ")}
            </Typography>
          )}
        </Box>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            flexWrap: "wrap",
            gap: 1.5,
          }}
        >
          {saved && (
            <Typography role="status" typography="s1" color="text.secondary">
              Profile saved.
            </Typography>
          )}
          <Button
            ref={editButton}
            size="small"
            variant="outlined"
            aria-expanded={editing}
            aria-controls={formId}
            onClick={editing ? close : open}
          >
            Edit profile
          </Button>
        </Box>
      </Box>
      {publisher.suspended && (
        <Alert severity="error">
          This publisher is suspended. New submissions are refused.
        </Alert>
      )}
      <Box
        component="form"
        id={formId}
        hidden={!editing}
        noValidate
        onSubmit={save}
        aria-label={`Profile of @${publisher.handle}`}
        sx={{
          display: editing ? "flex" : "none",
          flexDirection: "column",
          gap: 2,
          borderTop: "1px solid",
          borderColor: "divider",
          pt: 2,
        }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            flexWrap: "wrap",
            gap: 1.5,
          }}
        >
          <Button
            size="small"
            variant="outlined"
            disabled={avatar.isPending}
            onClick={() => avatarInput.current?.click()}
          >
            Upload an avatar
          </Button>
          <input
            ref={avatarInput}
            hidden
            type="file"
            accept="image/png,image/jpeg"
            onChange={(e) => {
              const chosen = e.target.files?.[0];
              if (chosen) avatar.mutate(chosen);
              e.target.value = "";
            }}
          />
          <Typography typography="s2" color="text.tertiary">
            PNG or JPEG, at most 256 KB. It is saved when you choose it.
          </Typography>
        </Box>
        {avatar.isError && (
          <Alert severity="error">
            {problemText(
              avatar.error,
              `The avatar was not uploaded. ${NO_ANSWER}`,
            )}
          </Alert>
        )}
        <Box
          sx={{
            display: "grid",
            gap: 1.5,
            alignItems: "start",
            gridTemplateColumns: {
              xs: "minmax(0, 1fr)",
              sm: "repeat(2, minmax(0, 1fr))",
            },
          }}
        >
          <TextField
            id={`${ids}-display-name`}
            size="small"
            label="Display name"
            value={form.displayName}
            onChange={set("displayName")}
            inputRef={firstField}
            inputProps={{ maxLength: 60 }}
          />
          <TextField
            id={`${ids}-website`}
            size="small"
            label="Website (https)"
            value={form.website}
            onChange={set("website")}
          />
          <TextField
            id={`${ids}-github`}
            size="small"
            label="GitHub login"
            value={form.githubLogin}
            onChange={set("githubLogin")}
            helperText="Shown as a link, never as proof"
          />
          <TextField
            id={`${ids}-bio`}
            size="small"
            label="Bio"
            value={form.bio}
            onChange={set("bio")}
            inputProps={{ maxLength: 160 }}
            helperText={`${form.bio.length} / 160`}
          />
        </Box>
        {update.isError && (
          <Alert severity="error">
            {problemText(
              update.error,
              `The profile was not saved. ${NO_ANSWER}`,
            )}
          </Alert>
        )}
        <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
          <Button
            type="submit"
            size="small"
            variant="contained"
            disabled={update.isPending}
          >
            Save profile
          </Button>
          <Button size="small" color="inherit" onClick={close}>
            Cancel
          </Button>
        </Box>
      </Box>
      {publisher.tier === "community" && (
        <Box
          sx={{
            borderTop: "1px solid",
            borderColor: "divider",
            pt: 2,
            display: "flex",
            flexDirection: "column",
            gap: 1,
          }}
        >
          <Typography
            component="h4"
            typography="s1"
            fontWeight="fontWeightSemiBold"
            color="text.primary"
          >
            Verified publisher
          </Typography>
          {publisher.verificationRequested ? (
            <Typography typography="s1" color="text.secondary">
              Requested. A maintainer will verify or decline it.
            </Typography>
          ) : (
            <>
              <Typography typography="s1" color="text.secondary">
                Show who you are: an email on your company domain, or a page on
                your site that names this handle.
              </Typography>
              <TextField
                id={`${ids}-evidence`}
                size="small"
                label="Evidence"
                value={evidence}
                onChange={(e) => setEvidence(e.target.value)}
                multiline
                minRows={2}
                inputProps={{ maxLength: 1000 }}
                sx={{ maxWidth: 640 }}
              />
              {verify.isError && (
                <Alert severity="error">
                  {problemText(
                    verify.error,
                    `The verification request was not sent. ${NO_ANSWER}`,
                  )}
                </Alert>
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
    </Box>
  );
}

PublisherProfile.propTypes = { publisher: PropTypes.object.isRequired };
