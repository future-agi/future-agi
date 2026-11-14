import {
  Avatar,
  Box,
  Checkbox,
  InputAdornment,
  Menu,
  MenuItem,
  TextField,
  Typography,
} from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import React, { useRef, useState } from "react";
import SvgColor from "src/components/svg-color";
import { useOrganization } from "src/contexts/OrganizationContext";
import DropdownWithSearch from "src/sections/common/DropdownWithSearch";
import axios, { endpoints } from "src/utils/axios";
import { stringAvatar } from "src/utils/utils";
import PropTypes from "prop-types";
import useListSearch from "src/hooks/use-list-search";

const FilterOptions = [
  { label: "Contains", value: "contains" },
  { label: "Does not contains", value: "does_not_contain" },
];

const AnnotatorsPopover = ({
  selectedAnnotators,
  setSelectedAnnotators,
  annotatorFilter,
  setAnnotatorFilter,
}) => {
  const menuAnchor = useRef(null);
  const [open, setOpen] = useState(false);
  const { currentOrganizationId } = useOrganization();

  const handleClose = () => {
    setOpen(false);
  };

  const { data: annotationUserApiData } = useQuery({
    queryKey: ["organizationId", currentOrganizationId],
    queryFn: () =>
      axios.get(endpoints.annotation.annotationsUser(currentOrganizationId), {
        params: { is_active: true },
      }),
    select: (data) => data?.data?.results,
    enabled: !!currentOrganizationId,
  });

  const { filteredList, listSearchQuery, onListSearch } = useListSearch(
    annotationUserApiData,
    "name",
  );

  return (
    <Box
      sx={{
        padding: "12px",
        display: "flex",
        flexDirection: "column",
        gap: "12px",
      }}
    >
      <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
        <TextField
          placeholder="Search"
          fullWidth
          size="small"
          value={listSearchQuery}
          onChange={(e) => onListSearch(e.target.value)}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SvgColor
                  src="/assets/icons/app/ic_search.svg"
                  sx={{ width: 16, height: 16 }}
                />
              </InputAdornment>
            ),
          }}
        />
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
          <Typography variant="s2" fontWeight={500} color="text.secondary">
            Annotators
          </Typography>
          <Box
            onClick={() => setOpen(true)}
            sx={{ display: "flex", alignItems: "center" }}
          >
            <Typography
              variant="s2"
              color="text.secondary"
              fontWeight={600}
              ref={menuAnchor}
              sx={{ cursor: "pointer" }}
            >
              {
                FilterOptions.find((option) => option.value === annotatorFilter)
                  ?.label
              }
            </Typography>
            <SvgColor
              src="/assets/icons/custom/lucide--chevron-down.svg"
              sx={{ color: "text.secondary", width: 16, height: 16 }}
            />
          </Box>
          <Menu
            id="basic-menu"
            anchorEl={menuAnchor.current}
            open={open}
            onClose={handleClose}
            MenuListProps={{
              "aria-labelledby": "basic-button",
            }}
          >
            {FilterOptions.map((option) => (
              <MenuItem
                key={option.value}
                onClick={() => {
                  setAnnotatorFilter(option.value);
                  handleClose();
                }}
              >
                {option.label}
              </MenuItem>
            ))}
          </Menu>
        </Box>
        <Box
          sx={{
            maxHeight: "100px",
            overflow: "auto",
            display: "flex",
            flexDirection: "column",
            gap: 0.5,
            cursor: "pointer",
          }}
        >
          {filteredList?.map((user) => {
            const isSelected = selectedAnnotators.includes(user.id);

            const toggleAnnotator = () => {
              if (isSelected) {
                setSelectedAnnotators(
                  selectedAnnotators.filter((id) => id !== user.id),
                );
              } else {
                setSelectedAnnotators([...selectedAnnotators, user.id]);
              }
            };

            return (
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 1,
                  backgroundColor: isSelected
                    ? "action.hover"
                    : "background.paper",
                  padding: 0.5,
                  borderRadius: 1,
                }}
                key={user.id}
                onClick={toggleAnnotator}
              >
                <Checkbox
                  checked={isSelected}
                  inputProps={{ "aria-label": "controlled" }}
                  sx={{
                    padding: 0,
                  }}
                />
                <Avatar
                  {...stringAvatar(user.name)}
                  sx={{
                    width: 24,
                    height: 24,


                    padding: 1,
                    fontSize: "12px",
                  }}
                />
                <Typography variant="s2" sx={{ userSelect: "none" }}>
                  {user.name}
                </Typography>
              </Box>
            );
          })}
        </Box>
      </Box>
    </Box>
  );
};

AnnotatorsPopover.displayName = "AnnotatorsPopover";

AnnotatorsPopover.propTypes = {
  selectedAnnotators: PropTypes.array,
  setSelectedAnnotators: PropTypes.func,
  annotatorFilter: PropTypes.string,
  setAnnotatorFilter: PropTypes.func,
};

const AnnotatorsDropdown = ({
  selectedAnnotators,
  setSelectedAnnotators,
  annotatorFilter,
  setAnnotatorFilter,
}) => {
  const anchorRef = useRef(null);

  return (
    <DropdownWithSearch
      displayEmpty
      size="small"
      label=""
      options={[{ label: "Annotators", value: "annotators" }]}
      value={selectedAnnotators}
      sx={{ width: "180px" }}
      multiple
      renderValue={() => {
        return (
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <SvgColor
              src="/assets/icons/custom/people.svg"
              sx={{
                color: "text.secondary",
                width: 16,
                height: 16,
                flexShrink: 0,
              }}
            />
            <Typography variant="s1">
              Annotators{" "}
              {selectedAnnotators.length > 0
                ? `(${selectedAnnotators.length})`
                : ""}
            </Typography>
          </Box>
        );
      }}
      popoverComponent={() => (
        <AnnotatorsPopover
          selectedAnnotators={selectedAnnotators}
          setSelectedAnnotators={setSelectedAnnotators}
          annotatorFilter={annotatorFilter}
          setAnnotatorFilter={setAnnotatorFilter}
        />
      )}
      anchorRef={anchorRef}
    />
  );
};

AnnotatorsDropdown.propTypes = {
  selectedAnnotators: PropTypes.array,
  setSelectedAnnotators: PropTypes.func,
  annotatorFilter: PropTypes.string,
  setAnnotatorFilter: PropTypes.func,
};

export default AnnotatorsDropdown;
