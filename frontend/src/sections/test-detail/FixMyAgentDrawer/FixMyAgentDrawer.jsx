import { Box, Collapse, Drawer, ButtonBase } from "@mui/material";
import PropTypes from "prop-types";
import React, { useRef } from "react";
import { ShowComponent } from "../../../components/show";
import SvgColor from "../../../components/svg-color";
import { useTestDetailStoreShallow } from "../states";
import SuggestionsPage from "./SuggestionsPage";
import { useFixMyAgentDrawerStoreShallow } from "./state";
import { FixMyAgentDrawerSections } from "./common";
import CreateEditOptimizationModal from "../CreateEditOptimization/CreateEditOptimizationModal";
import EachOptimizationComponent from "./EachOptimizationComponent";
import OptimizationDetailComponent from "./OptimizationDetail/OptimizationDetailComponent";
import logger from "../../../utils/logger";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

const FixMyAgentDrawerChild = ({ onClose }) => {
  const previousOpenSection = useRef(null);

  const { openSection, setOpenSection } = useFixMyAgentDrawerStoreShallow(
    (s) => ({
      openSection: s.openSection,
      setOpenSection: s.setOpenSection,
    }),
  );

  logger.debug({ openSection });

  const isFixMyAgentCollapsed =
    openSection.section === FixMyAgentDrawerSections.NONE;

  return (
    <Box sx={{ position: "relative", height: "100%", width: "100%" }}>
      <ButtonBase
        component="div"
        sx={{
          position: "absolute",
          top: 20,
          left: -30,
          width: "30px",
          height: "28px",
          backgroundColor: "background.paper",
          border: "1px solid",
          borderColor: "divider",
          borderRightColor: "transparent",
          borderTopLeftRadius: "4px",
          borderBottomLeftRadius: "4px",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          zIndex: 1301,
        }}
        onClick={() => {
          if (openSection.section === FixMyAgentDrawerSections.NONE) {
            const newContent = previousOpenSection.current
              ? { ...previousOpenSection.current }
              : { section: FixMyAgentDrawerSections.SUGGESTIONS };
            setOpenSection(newContent);
          } else {
            previousOpenSection.current = {
              section: openSection.section,
              id: openSection.id ?? null,
            };
            setOpenSection({ section: FixMyAgentDrawerSections.NONE });
          }
        }}
      >
        <SvgColor
          src="/assets/icons/custom/lucide--chevron-right.svg"
          sx={{
            width: 16,
            height: 16,
            transform: isFixMyAgentCollapsed
              ? "rotate(180deg)"
              : "rotate(0deg)",
            transition: "transform 0.2s ease-in-out",
          }}
        />
      </ButtonBase>

      <Collapse
        in={openSection?.section === FixMyAgentDrawerSections.SUGGESTIONS}
        orientation="horizontal"
        sx={{ height: "100%" }}
        unmountOnExit={
          openSection.section !== FixMyAgentDrawerSections.SUGGESTIONS &&
          openSection.section !== FixMyAgentDrawerSections.NONE
        }
      >
        <Box sx={{ width: "570px", height: "100%" }}>
          <SuggestionsPage onClose={onClose} />
        </Box>
      </Collapse>

      <Collapse
        in={openSection?.section === FixMyAgentDrawerSections.OPTIMIZE}
        orientation="horizontal"
        sx={{ height: "100%" }}
        unmountOnExit={
          openSection.section !== FixMyAgentDrawerSections.OPTIMIZE &&
          openSection.section !== FixMyAgentDrawerSections.NONE
        }
      >
        <Box sx={{ width: "80vw", height: "100%" }}>
          <EachOptimizationComponent
            optimizationId={openSection?.id}
            onClose={onClose}
          />
        </Box>
      </Collapse>

      <Collapse
        in={openSection?.section === FixMyAgentDrawerSections.TRIAL_DETAIL}
        orientation="horizontal"
        sx={{ height: "100%" }}
        unmountOnExit={
          openSection.section !== FixMyAgentDrawerSections.TRIAL_DETAIL &&
          openSection.section !== FixMyAgentDrawerSections.NONE
        }
      >
        <OptimizationDetailComponent
          optimizationId={openSection?.id}
          trialId={openSection?.trialId}
          onClose={onClose}
        />
      </Collapse>
    </Box>
  );
};

FixMyAgentDrawerChild.propTypes = {
  onClose: PropTypes.func,
};

const FixMyAgentDrawer = () => {
  const { fixMyAgentDrawerOpen, setFixMyAgentDrawerOpen } =
    useTestDetailStoreShallow((state) => ({
      fixMyAgentDrawerOpen: state.fixMyAgentDrawerOpen,
      setFixMyAgentDrawerOpen: state.setFixMyAgentDrawerOpen,
    }));

  const { openSection, setOpenSection } = useFixMyAgentDrawerStoreShallow(
    (s) => ({
      setOpenSection: s.setOpenSection,
      openSection: s.openSection,
    }),
  );

  const handleClose = () => {
    setFixMyAgentDrawerOpen(false);
    setOpenSection({ section: FixMyAgentDrawerSections.SUGGESTIONS });
  };

  const covered = useDrawerCovered(fixMyAgentDrawerOpen);
  return (
    <Drawer
      anchor="right"
      open={fixMyAgentDrawerOpen}
      sx={coveredDrawerSx(covered)}
      onClose={handleClose}
      variant="persistent"
      PaperProps={{
        sx: {
          borderLeft:
            openSection.section !== FixMyAgentDrawerSections.NONE
              ? "1px solid"
              : "none",
          borderColor: "divider",
          borderRadius: "0px !important",
          overflow: "visible",
        },
      }}
    >
      <ShowComponent condition={fixMyAgentDrawerOpen}>
        <FixMyAgentDrawerChild onClose={handleClose} />
      </ShowComponent>
      <CreateEditOptimizationModal />
    </Drawer>
  );
};

export default FixMyAgentDrawer;
