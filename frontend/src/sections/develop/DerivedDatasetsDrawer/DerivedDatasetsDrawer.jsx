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
  const theme = useTheme();

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

  return (
    <>
      <Drawer
        anchor="right"
        open={open}
        onClose={onClose}
        PaperProps={{
          sx: {
            height: "100vh",
            width: "550px",
            position: "fixed",
            zIndex: 9999,
            borderRadius: "10px",
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
          <Iconify icon="mingcute:close-line" color="text.primary" />
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
              variant="m3"
              fontWeight={"fontWeightSemiBold"}
              color="text.primary"
              sx={{ marginBottom: 2 }}
            >
              Derived Dataset
            </Typography>

            <List
              sx={{
                py: 0,
                width: "100%",
                borderRadius: "4px",
                border: `1px solid ${theme.palette.divider}`,
              }}
            >
              <ListItem sx={{ padding: 0 }}>
                <Typography
                  variant="s1"
                  color="text.primary"
                  fontWeight={"fontWeightMedium"}
                  sx={{
                    padding: "10px 15px",
                    borderBottom: `1px solid ${theme.palette.divider}`,
                    width: "100%",
                    backgroundColor: "background.neutral",
                    fontSize: "16px",
                  }}
                >
                  {datasetName}
                </Typography>
              </ListItem>
              {derivedDatasets?.map((dataset, index) => (
                <React.Fragment key={dataset.id}>
                  <ListItem
                    sx={{
                      padding: "10px 15px",
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      cursor: "pointer",
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
                        variant="s1"
                        color="text.primary"
                        fontWeight={"fontWeightRegular"}
                        noWrap
                        maxWidth={"400px"}
                      >
                        {dataset.name}
                      </Typography>
                    </CustomTooltip>

                    <Iconify
                      icon="mynaui:chevron-right"
                      width={20}
                      sx={{ color: "text.primary" }}
                    />
                  </ListItem>
                  {index !== derivedDatasets.length - 1 && (
                    <Divider
                      variant="middle"
                      component="li"
                      sx={{ backgroundColor: "action.hover" }}
                    />
                  )}
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

      <Box
        sx={{
          border: `1px solid ${theme.palette.divider}`,
          borderRadius: "4px",
          overflow: "hidden",
        }}
      >
        <Box
          sx={{
            padding: "10px 15px",
            backgroundColor: "background.neutral",
            borderBottom: `1px solid ${theme.palette.divider}`,
          }}
        >
          <Skeleton variant="text" width="60%" height={24} />
        </Box>

        {Array.from({ length: datasetCount }).map((_, index) => (
          <Box key={index}>
            <Box
              sx={{
                padding: "10px 15px",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <Skeleton variant="text" width="40%" height={20} />

              <Skeleton variant="circular" width={24} height={24} />
            </Box>

            {index !== datasetCount - 1 && (
              <Divider
                variant="middle"
                sx={{ backgroundColor: "action.hover" }}
              />
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
