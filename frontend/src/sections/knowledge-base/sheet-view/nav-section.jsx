import { Button, Skeleton, Stack, Typography, useTheme } from "@mui/material";
import React, { useMemo, useRef, useState } from "react";
import Iconify from "src/components/iconify";
import DropdownWithSearch from "../../common/DropdownWithSearch";
import KnowledgeSelectDropDown from "./knowledge-select-dropdown";
import SvgColor from "src/components/svg-color";
import { useNavigate, useParams } from "react-router";
import { useKnowledgeBaseList } from "src/api/knowledge-base/files";
import PropTypes from "prop-types";

export default function NavSection({
  lastUpdatedDate,
  setName,
  isFetchingData,
}) {
  const theme = useTheme();
  const plusRef = useRef(null);
  const [, setDropdownOpen] = useState(false);
  const navigate = useNavigate();
  const { knowledgeId } = useParams();

  const { data: knowledgeBaseList } = useKnowledgeBaseList();

  const knowledgeBaseOptions = useMemo(
    () =>
      knowledgeBaseList?.map(({ id, name }) => ({
        label: name,
        value: id,
      })),
    [knowledgeBaseList],
  );

  const renderValue = (value) => {
    if (!knowledgeBaseOptions) {
      return "Select a Knowledge base";
    }
    const selectedOption = knowledgeBaseOptions.find(
      (option) => option.value === value,
    );
    setName?.(selectedOption);
    return selectedOption ? selectedOption.label : "Select a Knowledge base";
  };

  const handleBackClick = () => {
    if (window.history.state?.idx > 0) {
      // Navigate back in history if possible
      navigate(-1);
    } else {
      navigate(`/dashboard/knowledge`, { replace: true });
    }
  };

  return (
    <Stack
      sx={{
        alignItems: "center",
      }}
      direction={"row"}
      justifyContent={"space-between"}
    >
      <Stack direction={"row"} gap={theme.spacing(1)} alignItems={"center"}>
        <Button
          onClick={handleBackClick}
          size="small"
          sx={{
            border: "1px solid",
            borderColor: "action.hover",
            color: "text.primary",
            display: "flex",
            alignItems: "center",
            flexDirection: "row",
            "& .MuiButton-startIcon": {
              marginRight: "4px",
            },
          }}
          startIcon={
            <Iconify
              icon="solar:alt-arrow-left-linear"
              width="16px"
              height="16px"
              sx={{ color: "text.primary" }}
            />
          }
        >
          Back
        </Button>
        <DropdownWithSearch
          value={knowledgeId}
          setValue={() => {}}
          options={knowledgeBaseList}
          renderValue={renderValue}
          sx={{
            width: 278,
            "& .MuiSelect-select": {
              py: theme.spacing(0.5),
              px: theme.spacing(1.5),
              borderRadius: "4px",
            },
          }}
          anchorRef={plusRef}
          ref={plusRef}
          popoverComponent={(props) => (
            <KnowledgeSelectDropDown
              {...props}
              onSelect={(value) => {
                setDropdownOpen(false);
                if (value?.value !== knowledgeId) {
                  navigate(`/dashboard/knowledge/${value?.value}`);
                }
              }}
              ref={plusRef}
            />
          )}
        />
      </Stack>
      <Stack direction={"row"} gap={theme.spacing(0.5)} alignItems={"center"}>
        <SvgColor
          sx={{
            height: "16px",
            width: "16px",
            color: "divider",
          }}
          src="/assets/icons/ic_reload.svg"
        />
        {isFetchingData ? (
          <Skeleton variant="text" height={18} width={116} />
        ) : (
          <Typography color={"text.disabled"} variant={"s2"}>
            Updated {new Date(lastUpdatedDate).toLocaleDateString()}
          </Typography>
        )}
      </Stack>
    </Stack>
  );
}

NavSection.propTypes = {
  lastUpdatedDate: PropTypes.string,
  setName: PropTypes.func,
  isFetchingData: PropTypes.bool,
};
