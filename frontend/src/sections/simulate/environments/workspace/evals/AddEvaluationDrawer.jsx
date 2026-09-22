import PropTypes from "prop-types";
import { Box, Stack, Typography, Button, CircularProgress } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { useAvailableEvaluations, useAddEvaluation } from "src/api/simulate-environments/environments";
import SideDrawer from "../../components/SideDrawer";
import EmptyState from "../../components/EmptyState";
import { modalityOf, mappingRowsFor, humanizeMappingTerm } from "./evalSourceMapping";

/**
 * §10 add-evaluation picker.
 *
 * The list comes from `GET …/evaluations/available/` (the catalogue filtered to
 * this environment's modality, minus what is already selected). Each eval shows
 * its input mapping — left the required key, right the source it reads — resolved
 * read-only from the environment's modality (the add endpoint takes only a name;
 * the backend resolves the mapping). Adding posts `{ name }`; the added eval
 * drops out of the list on the next fetch.
 */
export default function AddEvaluationDrawer({ open, env, onClose, onAdded }) {
  const envId = env?.id;
  const modality = modalityOf(env);
  const { data: evaluations = [], isLoading, isError, refetch } =
    useAvailableEvaluations(envId, { enabled: open });
  const addEval = useAddEvaluation();
  const addingName = addEval.isPending ? addEval.variables?.name : null;

  const add = (name) =>
    addEval.mutate({ id: envId, name }, { onSuccess: () => onAdded?.(name) });

  return (
    <SideDrawer open={open} onClose={onClose} width={520}>
      <Stack sx={{ height: "100%", minHeight: 0 }}>
        <Box sx={{ px: 3, pt: 3, pb: 2, flexShrink: 0 }}>
          <Typography sx={{ typography: "m2", fontWeight: "fontWeightSemiBold" }}>
            Add evaluations
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary", maxWidth: 420 }}>
            Pick from the library. Each eval’s inputs are mapped from this{" "}
            {modality === "voice" ? "voice" : "chat"} environment automatically.
          </Typography>
        </Box>

        <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", px: 3, pb: 3 }}>
          {isLoading ? (
            <Stack alignItems="center" sx={{ py: 6 }}>
              <CircularProgress size={22} />
            </Stack>
          ) : isError ? (
            <EmptyState
              icon="solar:danger-triangle-linear"
              title="Couldn’t load evaluations"
              body="Something went wrong fetching the library. Try again."
              action={
                <Button variant="outlined" size="small" onClick={() => refetch()}>
                  Retry
                </Button>
              }
            />
          ) : evaluations.length === 0 ? (
            <EmptyState
              icon="solar:shield-check-linear"
              title="Nothing left to add"
              body="Every evaluation this environment can be graded by is already applied."
            />
          ) : (
            <Stack spacing={1.5}>
              {evaluations.map((item) => (
                <EvalOffer
                  key={item.name}
                  item={item}
                  modality={modality}
                  adding={addingName === item.name}
                  disabled={addEval.isPending}
                  onAdd={() => add(item.name)}
                />
              ))}
            </Stack>
          )}
        </Box>
      </Stack>
    </SideDrawer>
  );
}

AddEvaluationDrawer.propTypes = {
  open: PropTypes.bool,
  env: PropTypes.shape({ id: PropTypes.string, agentType: PropTypes.string }),
  onClose: PropTypes.func,
  onAdded: PropTypes.func,
};

function EvalOffer({ item, modality, adding, disabled, onAdd }) {
  const rows = mappingRowsFor(item.required_keys, modality);
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1.25,
        p: 2,
      }}
    >
      <Stack direction="row" alignItems="flex-start" spacing={1.5}>
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s1", fontWeight: "fontWeightSemiBold" }}>
            {humanizeMappingTerm(item.name)}
          </Typography>
          {item.description && (
            <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
              {item.description}
            </Typography>
          )}
        </Box>
        <Button
          variant="outlined"
          size="small"
          disabled={disabled}
          onClick={onAdd}
          startIcon={
            adding ? (
              <CircularProgress size={13} color="inherit" />
            ) : (
              <Iconify icon="solar:add-circle-linear" width={15} />
            )
          }
          sx={{ typography: "s2", fontWeight: "fontWeightBold", flexShrink: 0 }}
        >
          {adding ? "Adding…" : "Add"}
        </Button>
      </Stack>

      {rows.length > 0 && (
        <Stack spacing={0.5} sx={{ mt: 1.5 }}>
          <Typography sx={{ typography: "s3", color: "text.disabled", letterSpacing: 0.3 }}>
            Inputs
          </Typography>
          {rows.map(({ key, value }) => (
            <Stack
              key={key}
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{ typography: "s3" }}
            >
              <Box
                sx={{
                  px: 0.75, py: 0.125, borderRadius: 0.75,
                  bgcolor: "background.neutral", color: "text.secondary",
                  fontFamily: "ui-monospace, Menlo, monospace", fontSize: 11,
                }}
              >
                {humanizeMappingTerm(key)}
              </Box>
              <Iconify icon="solar:arrow-right-linear" width={13} sx={{ color: "text.disabled" }} />
              <Box
                sx={{
                  px: 0.75, py: 0.125, borderRadius: 0.75,
                  fontFamily: "ui-monospace, Menlo, monospace", fontSize: 11,
                  color: value ? "text.primary" : "text.disabled",
                  bgcolor: (t) =>
                    value ? alpha(t.palette.primary.main, 0.1) : "transparent",
                }}
              >
                {value ? humanizeMappingTerm(value) : "—"}
              </Box>
            </Stack>
          ))}
        </Stack>
      )}
    </Box>
  );
}

EvalOffer.propTypes = {
  item: PropTypes.shape({
    name: PropTypes.string,
    description: PropTypes.string,
    required_keys: PropTypes.arrayOf(PropTypes.string),
    modality: PropTypes.string,
  }),
  modality: PropTypes.string,
  adding: PropTypes.bool,
  disabled: PropTypes.bool,
  onAdd: PropTypes.func,
};
