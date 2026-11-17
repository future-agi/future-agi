import { useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  FormControlLabel,
  Radio,
  RadioGroup,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useAuthContext } from "src/auth/hooks";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import Section from "src/components/section";
import { fDateTimeLocal, fToNow } from "src/utils/format-time";
import { ROLES } from "src/utils/rolePermissionMapping";
import { getErrorMessage } from "../../settings/integrations/utils";
import {
  useRequestLogRetention,
  useRequestLogRetentionPreview,
  useSaveRequestLogRetention,
} from "./hooks/useRequestLogRetention";

const isValidDays = (value, minimum) =>
  /^\d+$/.test(value) &&
  Number.isSafeInteger(Number(value)) &&
  Number(value) >= Math.max(1, minimum ?? 1);

function CleanupTime({ value }) {
  if (!value) return "—";
  return (
    <>
      {fDateTimeLocal(value)} ({fToNow(value)})
    </>
  );
}

CleanupTime.propTypes = { value: PropTypes.string };

export default function RequestLogRetentionCard() {
  const { role } = useAuthContext();
  const canSave = role === ROLES.ADMIN || role === ROLES.OWNER;
  const { data, isLoading, isError, error, refetch } = useRequestLogRetention();
  const save = useSaveRequestLogRetention();
  const [mode, setMode] = useState("forever");
  const [days, setDays] = useState("30");
  const [debouncedDays, setDebouncedDays] = useState(null);
  const savedDays = data?.retention_days;
  const keepForeverRequired = data?.keep_forever_required;
  const lastRun = data?.last_run;
  const runInProgress =
    lastRun?.started_at &&
    (!lastRun.finished_at ||
      new Date(lastRun.started_at) > new Date(lastRun.finished_at));
  const runCutShort =
    runInProgress &&
    Date.now() - new Date(lastRun.started_at).getTime() >= 65 * 60 * 1000;

  useEffect(() => {
    if (savedDays === undefined) return;
    setMode(keepForeverRequired || savedDays === null ? "forever" : "days");
    if (savedDays !== null) setDays(String(savedDays));
  }, [savedDays, keepForeverRequired]);

  const validDays = isValidDays(days, data?.min_retention_days);
  const selectedDays = mode === "days" && validDays ? Number(days) : null;
  const isPreviewing =
    selectedDays !== null && selectedDays !== data?.retention_days;

  useEffect(() => {
    if (!isPreviewing || data?.keep_forever_required) {
      setDebouncedDays(null);
      return undefined;
    }
    const timer = setTimeout(() => setDebouncedDays(selectedDays), 400);
    return () => clearTimeout(timer);
  }, [isPreviewing, selectedDays, data?.keep_forever_required]);

  const preview = useRequestLogRetentionPreview(
    isPreviewing &&
      !data?.keep_forever_required &&
      debouncedDays === selectedDays
      ? debouncedDays
      : null,
  );
  const estimate = isPreviewing && preview.data ? preview.data : data;
  const retentionDays = mode === "forever" ? null : Number(days);
  const changed = retentionDays !== data?.retention_days;
  const cannotSave =
    !canSave ||
    save.isPending ||
    !changed ||
    (mode === "days" && (!validDays || data?.keep_forever_required));

  const handleSave = () => {
    if (cannotSave) return;
    save.mutate(retentionDays);
  };

  return (
    <Section
      variant="panel"
      title="Request Logs retention"
      description="Choose how long to keep gateway Request Logs."
    >
      {isLoading ? (
        <ContentSkeleton
          variant="panel"
          label="Loading Request Logs retention"
        />
      ) : isError ? (
        <ErrorState
          title="Couldn't load Request Logs retention"
          detail={getErrorMessage(error, "Please try again.")}
          onRetry={refetch}
        />
      ) : (
        <Stack spacing={2}>
          <Box>
            <RadioGroup
              aria-label="Request Logs retention"
              value={mode}
              onChange={(event) => {
                setMode(event.target.value);
                save.reset();
              }}
            >
              <FormControlLabel
                value="forever"
                control={<Radio size="small" />}
                label="Keep forever"
              />
              <FormControlLabel
                value="days"
                disabled={data.keep_forever_required}
                control={<Radio size="small" />}
                label="Delete after N days"
              />
            </RadioGroup>
            {data.keep_forever_required ? (
              <Typography variant="s3" color="text.secondary">
                A total budget is configured, so Request Logs are kept forever.
              </Typography>
            ) : (
              <Stack spacing={0.5} sx={{ mt: 1, ml: 4 }}>
                <Typography
                  component="label"
                  htmlFor="retention-days"
                  variant="s3"
                  color="text.secondary"
                >
                  Days
                </Typography>
                <TextField
                  id="retention-days"
                  type="number"
                  size="small"
                  value={days}
                  disabled={mode !== "days"}
                  onChange={(event) => {
                    setDays(event.target.value);
                    save.reset();
                  }}
                  inputProps={{
                    min: Math.max(1, data.min_retention_days ?? 1),
                    step: 1,
                  }}
                  error={mode === "days" && !validDays}
                  helperText={
                    mode === "days" && !validDays
                      ? `Enter a whole number of at least ${Math.max(1, data.min_retention_days ?? 1)} days.`
                      : undefined
                  }
                  sx={{ maxWidth: 180 }}
                />
                {data.min_retention_days != null && (
                  <Typography variant="s3" color="text.secondary">
                    At least {data.min_retention_days} days: a budget needs
                    them.
                  </Typography>
                )}
              </Stack>
            )}
          </Box>

          {save.isError && (
            <Alert severity="error" role="alert">
              {getErrorMessage(
                save.error,
                "Could not save retention. Please try again.",
              )}
            </Alert>
          )}
          <Box>
            <LoadingButton
              variant="contained"
              size="small"
              disabled={cannotSave}
              loading={save.isPending}
              onClick={handleSave}
            >
              Save
            </LoadingButton>
          </Box>

          <Stack
            spacing={1}
            sx={{ borderTop: 1, borderColor: "divider", pt: 1.5 }}
          >
            <Box>
              <Typography variant="s3" color="text.secondary" component="div">
                Next cleanup
              </Typography>
              <Typography variant="s1">
                <CleanupTime value={data.next_run_at} />
              </Typography>
              <Typography variant="s3" color="text.secondary">
                Each run keeps the current period of every budget, and by
                default the current calendar month and week (UTC) for the
                gateway&apos;s own budgets.
              </Typography>
            </Box>
            <Box>
              <Typography variant="s3" color="text.secondary" component="div">
                Estimated rows removed on next run
              </Typography>
              <Typography variant="s1">
                {`About ${(estimate?.estimated_rows ?? 0).toLocaleString()}${estimate?.estimate_capped ? "+" : ""} rows`}
              </Typography>
              {isPreviewing && !preview.data && !preview.isError && (
                <Typography variant="s3" color="text.secondary">
                  Updating estimate… Showing the saved estimate for now.
                </Typography>
              )}
              {isPreviewing && preview.isError && (
                <Typography variant="s3" color="text.secondary">
                  Preview unavailable. Showing the saved estimate.
                </Typography>
              )}
              {changed && mode === "forever" && (
                <Typography variant="s3" color="text.secondary">
                  Showing the saved estimate until you save.
                </Typography>
              )}
            </Box>
            <Box>
              <Typography variant="s3" color="text.secondary" component="div">
                Last run
              </Typography>
              {lastRun?.started_at || lastRun?.finished_at ? (
                <Stack spacing={0.5}>
                  {runInProgress ? (
                    <>
                      <Typography
                        variant="s1"
                        color={runCutShort ? "status.warning.fg" : undefined}
                      >
                        {runCutShort ? "Did not finish" : "In progress"} ·
                        started <CleanupTime value={lastRun.started_at} />
                      </Typography>
                      {lastRun.finished_at && (
                        <Typography variant="s3" color="text.secondary">
                          Previous run:{" "}
                          <CleanupTime value={lastRun.finished_at} />
                          {" · "}
                          {(lastRun.deleted_rows ?? 0).toLocaleString()} rows
                          deleted
                        </Typography>
                      )}
                    </>
                  ) : (
                    <Typography variant="s1">
                      <CleanupTime value={lastRun.finished_at} /> ·{" "}
                      {(lastRun.deleted_rows ?? 0).toLocaleString()} rows
                      deleted
                    </Typography>
                  )}
                  {lastRun.error && (
                    <Alert severity="error">
                      {runInProgress
                        ? `Previous run: ${lastRun.error}`
                        : lastRun.error}
                    </Alert>
                  )}
                </Stack>
              ) : (
                <Typography variant="s1">Not run yet</Typography>
              )}
            </Box>
          </Stack>
        </Stack>
      )}
    </Section>
  );
}
