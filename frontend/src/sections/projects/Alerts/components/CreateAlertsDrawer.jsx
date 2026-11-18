import {
  Box,
  Button,
  Drawer,
  IconButton,
  Stack,
  Typography,
  useTheme,
  Link,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { lazy, Suspense, useEffect, useMemo, useState } from "react";
import SvgColor from "src/components/svg-color";
import AlertTabs from "./AlertTabs";
import { ShowComponent } from "src/components/show";
import { getDefaultDateRange } from "../common";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useAlertStore } from "../store/useAlertStore";
import {
  SelectAlertTypeSkeleton,
  AlertConfigFormSkeleton,
} from "./AlertSkeletons";
const SelectAlertType = lazy(() => import("./SelectAlertType"));
const AlertConfiguration = lazy(() => import("./AlertConfiguration"));
import { ConfirmDialog } from "src/components/custom-dialog";

const tabs = [
  {
    title: "Select Alert Type",
  },
  {
    title: "Set Alert Configuration",
  },
];

export default function CreateAlertsDrawer({ open }) {
  const theme = useTheme();
  const {
    alertType,
    handleChangeAlertType,
    openSheetView,
    duplicateAlertName,
    currentTab,
    setCurrentTab,
    handleCloseCreateAlert,
    isConfirmationModalOpen,
    setConfirmationModalOpen,
  } = useAlertStore();
  const [dateFilter, setDateFilter] = useState(getDefaultDateRange());
  const [nextClicked, setNextClicked] = useState(false);

  const handleTrackEventAlertTypeSelected = () => {
    if (!alertType) return;
    trackEvent(Events.alertTypeClicked, {
      [PropertyName.type]: alertType,
    });
  };

  const handleChangeCurrentTab = (index) => {
    if (typeof index !== "number") {
      setCurrentTab(0);
      return;
    }

    if (index === 1 && !alertType) {
      return;
    }
    if (index === 1) {
      handleTrackEventAlertTypeSelected();
      setNextClicked(true);
    }
    setCurrentTab(index);
  };

  const handleNext = () => {
    setCurrentTab(1);
    setNextClicked(true);
    handleTrackEventAlertTypeSelected();
  };

  useEffect(() => {
    if (openSheetView) {
      setCurrentTab(1);
    }
  }, [openSheetView, setCurrentTab]);

  const completedSteps = useMemo(() => {
    if (openSheetView || nextClicked) {
      return [0];
    }

    return [];
  }, [nextClicked, openSheetView]);

  const handleConfirmClose = () => {
    handleCloseCreateAlert();
    setConfirmationModalOpen(false);
  };

  const alertOption = () => {
    return openSheetView
      ? duplicateAlertName
        ? "Duplicate "
        : "Edit "
      : "Create ";
  };

  return (
    <>
      <Drawer
        anchor={"bottom"}
        open={open}
        onClose={() => setConfirmationModalOpen(true)}
        PaperProps={{
          sx: {
            height: "100vh",
            maxHeight: "100vh",
            overflow: "auto",
            borderRadius: "0 !important",
            padding: theme.spacing(2, 2.5),
            paddingTop: 0,
          },
        }}
        SlideProps={{
          onExited: () => {
            setNextClicked(false);
          },
        }}
        ModalProps={{
          hideBackdrop: true,
        }}
      >
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 2,
          }}
        >
          <Stack
            gap={2}
            sx={{
              backgroundColor: "background.paper",
              position: "sticky",
              top: 0,
              zIndex: 20,
              paddingTop: theme.spacing(2),
            }}
          >
            <Stack
              flexDirection={"row"}
              justifyContent={"space-between"}
              alignItems={"center"}
            >
              <Box
                display="flex"
                alignItems="center"
                justifyContent="space-between"
                width="97%"
              >
                <Typography
                  variant="m3"
                  color={"text.primary"}
                  fontWeight={"fontWeightSemiBold"}
                >
                  {alertOption()}
                  Alerts
                </Typography>
                <Link
                  href="https://docs.futureagi.com/docs/observe/features/alerts"
                  underline="always"
                  color="text.link"
                  target="_blank"
                  rel="noopener noreferrer"
                  fontWeight="fontWeightMedium"
                >
                  Learn more
                </Link>
              </Box>
              <IconButton
                size="small"
                onClick={() => setConfirmationModalOpen(true)}
                sx={{
                  margin: 0,
                }}
              >
                <SvgColor
                  sx={{
                    color: "text.primary",
                    height: theme.spacing(3),
                    width: theme.spacing(3),
                  }}
                  src="/assets/icons/ic_close.svg"
                />
              </IconButton>
            </Stack>
            <AlertTabs
              tabs={tabs}
              currentTab={currentTab}
              completedIndexes={completedSteps}
              onChange={handleChangeCurrentTab}
            />
          </Stack>
          <ShowComponent condition={currentTab === 0}>
            <Suspense fallback={<SelectAlertTypeSkeleton />}>
              <SelectAlertType
                alertType={alertType}
                onChange={handleChangeAlertType}
                onCancel={() => setConfirmationModalOpen(true)}
                handleNext={handleNext}
              />
            </Suspense>
          </ShowComponent>
          <ShowComponent condition={currentTab === 1}>
            <Suspense fallback={<AlertConfigFormSkeleton />}>
              <AlertConfiguration
                dateFilter={dateFilter}
                setDateFilter={setDateFilter}
              />
            </Suspense>
          </ShowComponent>
        </Box>
      </Drawer>
      <ConfirmDialog
        content="Are you sure you want to close? Your work will be lost"
        action={
          <Button
            variant="contained"
            color="error"
            size="small"
            onClick={handleConfirmClose}
          >
            Confirm
          </Button>
        }
        open={isConfirmationModalOpen}
        onClose={() => setConfirmationModalOpen(false)}
        title="Confirm Action"
        message="Are you sure you want to proceed?"
      />
    </>
  );
}

CreateAlertsDrawer.displayName = "CreateAlertsDrawer";

CreateAlertsDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
};
