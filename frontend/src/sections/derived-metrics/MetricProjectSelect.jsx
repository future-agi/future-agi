import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import { Autocomplete } from "@mui/material";
import TextField from "./MetricField";

const MORE = "__more_projects__";

/** One searchable control; catalog paging never changes the selected project. */
export default function MetricProjectSelect({
  value,
  projects,
  search,
  page,
  onSearch = () => {},
  onPage = () => {},
  onChange,
  disabled,
  readOnly,
}) {
  const [catalog, setCatalog] = useState({ search, rows: [] });
  const [selected, setSelected] = useState(null);
  const [open, setOpen] = useState(false);
  const [inputValue, setInputValue] = useState("");
  const searching = useRef(false);
  const loadingMore = useRef(false);
  useEffect(() => {
    if (!projects.data) return;
    const project = projects.data.rows.find((row) => row.id === value);
    if (project) setSelected(project);
    setCatalog((previous) => ({
      search,
      rows: Array.from(
        new Map(
          [
            ...(previous.search === search && page > 0 ? previous.rows : []),
            ...projects.data.rows,
          ].map((project) => [project.id, project]),
        ).values(),
      ),
    }));
  }, [projects.data, search, page, value]);
  const rows = catalog.search === search ? catalog.rows : [];
  const selection = value
    ? rows.find((project) => project.id === value) ||
      projects.data?.rows.find((project) => project.id === value) ||
      (selected?.id === value ? selected : { id: value, name: "Saved project" })
    : null;
  const hasMore = page + 1 < (projects.data?.totalPages || 0);
  // Autocomplete needs its saved value in the option catalog even when the
  // server's current search/page does not contain it. Do not offer that extra
  // value as a search match: only fetched rows appear in the popup below.
  const options =
    selection && !rows.some((project) => project.id === selection.id)
      ? [selection, ...rows]
      : rows;
  return (
    <Autocomplete
      fullWidth
      size="small"
      open={open && !readOnly}
      onOpen={() => setOpen(true)}
      onClose={(_, reason) => {
        if (reason === "selectOption" && loadingMore.current) {
          loadingMore.current = false;
          return;
        }
        setOpen(false);
      }}
      onBlur={() => {
        // A search is not a selection. Restore the selected label when leaving
        // the control, while catalog updates still leave active typing alone.
        searching.current = false;
        setInputValue(selection?.name || "");
      }}
      readOnly={readOnly}
      disabled={disabled}
      value={selection}
      inputValue={inputValue}
      options={
        hasMore
          ? [...options, { id: MORE, name: "Load more projects" }]
          : options
      }
      getOptionLabel={(option) => option.name}
      isOptionEqualToValue={(option, chosen) => option.id === chosen.id}
      filterOptions={(available) =>
        available.filter(
          (option) =>
            option.id === MORE ||
            rows.some((project) => project.id === option.id),
        )
      }
      loading={projects.isFetching}
      noOptionsText={
        projects.isError
          ? "Projects unavailable. Retry below."
          : "No projects found"
      }
      onInputChange={(_, text, reason) => {
        if (reason === "reset") {
          // Catalog responses must not replace a query currently being typed,
          // including when a recovered draft's project is outside this page.
          if (!searching.current) setInputValue(text);
        } else if (
          !readOnly &&
          !disabled &&
          ["input", "clear"].includes(reason)
        ) {
          searching.current = reason === "input";
          setInputValue(text);
          onSearch(text);
        }
      }}
      onChange={(_, project) => {
        if (readOnly || disabled) return;
        if (project?.id === MORE) {
          loadingMore.current = true;
          if (!projects.isFetching) onPage(page + 1);
          return;
        }
        searching.current = false;
        setInputValue(project?.name || "");
        setSelected(project);
        onChange(project?.id || "");
      }}
      getOptionDisabled={(option) => option.id === MORE && projects.isFetching}
      renderInput={(params) => (
        <TextField
          {...params}
          label="Project"
          size="small"
          helperText={
            readOnly ? "Project cannot be changed after creation." : undefined
          }
        />
      )}
    />
  );
}
MetricProjectSelect.propTypes = {
  value: PropTypes.string,
  projects: PropTypes.object.isRequired,
  search: PropTypes.string,
  page: PropTypes.number,
  onSearch: PropTypes.func,
  onPage: PropTypes.func,
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
  readOnly: PropTypes.bool,
};
