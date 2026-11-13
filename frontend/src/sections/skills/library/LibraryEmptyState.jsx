import PropTypes from "prop-types";
import { Button } from "@mui/material";
import EmptyState from "src/components/empty-state";

export default function LibraryEmptyState({ onCreate, onImport }) {
  return (
    <EmptyState variant="first-run" title="Give your agents a shared library" icon="solar:book-bookmark-linear"
      description="Skills are instructions an agent loads when it needs them. Agent contexts combine standing instructions with linked skills."
      action={<Button variant="contained" color="primary" onClick={() => onCreate("blank")}>Create your first skill</Button>}
      secondaryAction={<Button variant="outlined" onClick={onImport}>Import a folder or ZIP</Button>} />
  );
}

LibraryEmptyState.propTypes = { onCreate: PropTypes.func.isRequired, onImport: PropTypes.func.isRequired };
