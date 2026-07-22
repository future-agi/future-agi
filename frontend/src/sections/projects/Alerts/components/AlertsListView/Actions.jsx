import { Badge, Box, Button, IconButton, Stack } from "@mui/material";
import React, { useRef, useState } from "react";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import RowActions from "./RowActions";
import ColumnDropdown from "src/components/ColumnDropdown/ColumnDropdown";
import { camelCase } from "lodash";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useAlertStore } from "../../store/useAlertStore";
import { useAlertFilterShallow } from "../../store/useAlertFilterStore";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

export default function Actions() {
  const { role } = useAuthContext();
  const {
    searchQuery,
    onSearchQueryChange,
    handleStartCreatingAlerts,
    selectedRows,
    columns,
    setColumns,
    selectedAll,
    setCurrentTab,
    selectedProject,
    handleOpenProjectModal,
    mainPage,
  } = useAlertStore();
  const { hasValidFilters, toggleFilter } = useAlertFilterShallow();

  const columnConfigureRef = useRef();
  const [openColumnConfigure, setOpenColumnConfigure] = useState(false);

  const onColumnVisibilityChange = (columnId) => {
    const newColumnData = columns.map((col) =>
      camelCase(col.id) === columnId
        ? { ...col, isVisible: !col.isVisible }
        : col,
    );

    setColumns(newColumnData);
  };

  const handleOnNewAlert = () => {
    trackEvent(Events.createNewAlertClicked, {
      [PropertyName.click]: true,
    });
    if (selectedProject) {
      handleStartCreatingAlerts();
      setCurrentTab(0);
    } else {
      handleOpenProjectModal();
    }
  };

  return (
    <>
      <Stack
        direction={"row"}
        alignItems={"center"}
        justifyContent={"space-between"}
      >
        <FormSearchField
          size="small"
          placeholder="Search"
          sx={{
            minWidth: "250px",
            "& .MuiOutlinedInput-root": { height: "30px" },
          }}
          searchQuery={searchQuery}
          onChange={(e) => onSearchQueryChange(e.target.value)}
        />
        {selectedAll || selectedRows?.length > 0 ? (
          <RowActions />
        ) : (
          <Stack direction={"row"} gap={2.5}>
            <IconButton
              onClick={() => toggleFilter(mainPage)}
              size="small"
              sx={{
                color: "text.primary",
              }}
            >
              {hasValidFilters ? (
                <Badge
                  variant="dot"
                  color="error"
                  overlap="circular"
                  anchorOrigin={{ vertical: "top", horizontal: "right" }}
                  sx={{
                    "& .MuiBadge-badge": {
                      top: 1,
                      right: 1,
                    },
                  }}
                >
                  <SvgColor
                    src="/assets/icons/components/ic_filter.svg"
                    sx={{ height: 16, width: 16 }}
                  />
                </Badge>
              ) : (
                <SvgColor
                  src="/assets/icons/components/ic_filter.svg"
                  sx={{ height: 16, width: 16 }}
                />
              )}
            </IconButton>
            <IconButton
              ref={columnConfigureRef}
              onClick={() => setOpenColumnConfigure(true)}
              size="small"
              sx={{
                color: "text.primary",
              }}
            >
              <SvgColor
                sx={{
                  height: 16,
                  width: 16,
                }}
                src="/assets/icons/action_buttons/ic_column.svg"
              />
            </IconButton>
            <Box sx={{ display: "flex", gap: 1.5, alignItems: "center" }}>
              <Button
                variant="outlined"
                size="small"
                sx={{
                  color: "text.primary",
                  borderColor: "divider",
                  padding: 1.5,
                  fontSize: "14px",
                  height: "38px",
                }}
                startIcon={<SvgColor src="/assets/icons/ic_docs_single.svg" />}
                component="a"
                href="https://docs.futureagi.com/docs/observe/features/alerts"
                target="_blank"
              >
                View Docs
              </Button>
              <Button
                variant="contained"
                color="primary"
                data-alert-action="new"
                sx={{
                  px: "24px",
                  borderRadius: "8px",
                  height: "38px",
                }}
                startIcon={
                  <Iconify
                    icon="octicon:plus-24"
                    color="background.paper"
                    sx={{
                      width: "20px",
                      height: "20px",
                    }}
                  />
                }
                onClick={handleOnNewAlert}
                disabled={
                  !RolePermission.OBSERVABILITY[PERMISSIONS.ADD_TASKS_ALERTS][
                    role
                  ]
                }
              >
                New Alert
              </Button>
            </Box>
          </Stack>
        )}
      </Stack>
      <ColumnDropdown
        open={openColumnConfigure}
        onClose={() => setOpenColumnConfigure(false)}
        anchorEl={columnConfigureRef?.current}
        columns={columns?.map((col) => ({
          ...col,
          id: camelCase(col.id),
        }))}
        onColumnVisibilityChange={onColumnVisibilityChange}
        setColumns={setColumns}
        defaultGrouping="Data columns"
      />
    </>
  );
}
