import {
  Box,
  Divider,
  Drawer,
  IconButton,
  List,
  ListItem,
  Skeleton,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import axios, { endpoints } from "src/utils/axios";
import logger from "src/utils/logger";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

const DerivedDatasetsDrawer = ({
  open,
  onClose,
  id,
  datasetName,
  datasetCount,
}) => {
  const [derivedDatasets, setDerivedDatasets] = useState([]);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    localStorage.setItem("derivedDatasetId", id);
    const fetchData = async () => {
      try {
        setLoading(true);
        const response = await axios.get(
          endpoints.develop.getDerivedDatasets(id),
        );
        if (response?.data?.result) {
          setLoading(false);
        }
        setDerivedDatasets(response?.data?.result);
      } catch (err) {
        setLoading(false);
        logger.error("Error fetching data:", err);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, [id]);

  const handleClick = (id) => {
    navigate(`individual-experiment/${id}`);
  };

  const covered = useDrawerCovered(open);
  return (
    <>
      <Drawer
        anchor="right"
        open={open}
        sx={coveredDrawerSx(covered)}
        onClose={onClose}
        PaperProps={{
          sx: {
            height: "100vh",
            width: "480px",
            position: "fixed",
            zIndex: 9999,
            borderRadius: "8px",
            backgroundColor: "background.paper",
          },
        }}
        ModalProps={{
          BackdropProps: {
            style: { backgroundColor: "transparent" },
          },
        }}
      >
        <IconButton
          onClick={onClose}
          sx={{ position: "absolute", top: "10px", right: "12px" }}
        >
          <Iconify icon="solar:close-linear" color="text.primary" />
        </IconButton>
        {loading ? (
          <DrivedDataSkeleton datasetCount={datasetCount} />
        ) : (
          <Box
            sx={{
              padding: 2,
              gap: 0,
              display: "flex",
              flexDirection: "column",
            }}
          >
            <Typography
              typography="title_xs"
              color="text.primary"
              sx={{ marginBottom: 1.5 }}
            >
              Derived Dataset
            </Typography>

            <List
              sx={{
                py: 0,
                width: "100%",
              }}
            >
              <ListItem sx={{ padding: 0 }}>
                <Typography
                  fontSize={13}
                  lineHeight="20px"
                  color="text.primary"
                  fontWeight={"fontWeightMedium"}
                  sx={{
                    padding: "8px 12px",
                    borderBottom: "1px solid",
                    borderColor: "border.subtle",
                    width: "100%",
                  }}
                >
                  {datasetName}
                </Typography>
              </ListItem>
              {derivedDatasets?.map((dataset, index) => (
                <React.Fragment key={dataset.id}>
                  <ListItem
                    sx={{
                      padding: "8px 12px",
                      minHeight: 36,
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      cursor: "pointer",
                      borderBottom:
                        index !== derivedDatasets.length - 1
                          ? "1px solid"
                          : "none",
                      borderColor: "border.subtle",
                    }}
                    onClick={() => {
                      trackEvent(Events.derivedDatasetOpened, {
                        datasetId: dataset.id,
                        [PropertyName.name]: dataset.name,
                      });

                      handleClick(dataset.id);
                    }}
                  >
                    <CustomTooltip
                      show
                      title={dataset.name}
                      arrow
                      placement="bottom"
                    >
                      <Typography
                        fontSize={13}
                        lineHeight="20px"
                        color="text.primary"
                        fontWeight={"fontWeightRegular"}
                        noWrap
                        maxWidth={"400px"}
                      >
                        {dataset.name}
                      </Typography>
                    </CustomTooltip>

                    <Iconify
                      icon="solar:alt-arrow-right-linear"
                      width={16}
                      sx={{ color: "text.tertiary" }}
                    />
                  </ListItem>
                </React.Fragment>
              ))}
            </List>
          </Box>
        )}
      </Drawer>
    </>
  );
};

DerivedDatasetsDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  refreshGrid: PropTypes.func,
  id: PropTypes.string,
  datasetName: PropTypes.string,
  datasetCount: PropTypes.number,
};

export default DerivedDatasetsDrawer;

const DrivedDataSkeleton = ({ datasetCount = 5 }) => {
  const theme = useTheme();

  return (
    <Box
      sx={{
        padding: "16px",
        display: "flex",
        flexDirection: "column",
        gap: "16px",
      }}
    >
      <Skeleton variant="text" width={120} height={28} />

      <Box sx={{ overflow: "hidden" }}>
        <Box
          sx={{
            padding: "8px 12px",
            borderBottom: `1px solid ${theme.palette.border.subtle}`,
          }}
        >
          <Skeleton variant="text" width="60%" height={20} />
        </Box>

        {Array.from({ length: datasetCount }).map((_, index) => (
          <Box key={index}>
            <Box
              sx={{
                padding: "8px 12px",
                minHeight: 36,
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <Skeleton variant="text" width="40%" height={20} />

              <Skeleton variant="circular" width={16} height={16} />
            </Box>

            {index !== datasetCount - 1 && (
              <Divider sx={{ borderColor: "border.subtle" }} />
            )}
          </Box>
        ))}
      </Box>
    </Box>
  );
};

DrivedDataSkeleton.propTypes = {
  datasetCount: PropTypes.number,
};
