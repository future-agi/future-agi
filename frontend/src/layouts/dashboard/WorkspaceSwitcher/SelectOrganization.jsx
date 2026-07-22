import { Box, Button, Popper, Skeleton, Typography } from "@mui/material";
import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { useQuery, useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import SVGColor from "src/components/svg-color";
import { ShowComponent } from "../../../components/show";
import { useOrganization } from "src/contexts/OrganizationContext";
import { useCreateOrganizationModal } from "../states";
import Iconify from "src/components/iconify";
import { Events, trackEvent } from "src/utils/Mixpanel";

const SelectOrganizationChild = React.forwardRef(
  ({ setOpen }, popperTimeoutRef) => {
    const { currentOrganizationId, switchOrganization } = useOrganization();

    const { data, isPending } = useQuery({
      queryFn: () => axios.get(endpoints.organizations.list),
      queryKey: ["organizations-list"],
      staleTime: 30_000,
    });

    const { mutate: doSwitch, isPending: isSwitching } = useMutation({
      mutationFn: (newId) => switchOrganization(newId),
      onSuccess: (_response, newId) => {
        trackEvent(Events.organizationSwitched || "organization_switched", {
          organizations: {
            oldOrganizationId: currentOrganizationId,
            newOrganizationId: newId,
          },
        });
      },
      onError: () => {
        // switchOrganization already shows a snackbar on error
      },
    });

    const organizations = useMemo(() => {
      const result = data?.data?.result || data?.data || {};
      return result.organizations || [];
    }, [data]);

    return (
      <Box
        onMouseEnter={() => {
          if (popperTimeoutRef.current) {
            clearTimeout(popperTimeoutRef.current);
            popperTimeoutRef.current = null;
          }
          setOpen(true);
        }}
        onMouseLeave={() => {
          popperTimeoutRef.current = setTimeout(() => {
            setOpen(false);
          }, 100);
        }}
        sx={{
          backgroundColor: "background.paper",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          boxShadow: "0px 4px 20px rgba(0, 0, 0, 0.1)",
          p: 1,
          minWidth: "200px",
          display: "flex",
          flexDirection: "column",
          gap: "12px",
        }}
      >
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 1,
            maxHeight: "200px",
            overflowY: "auto",
          }}
        >
          <ShowComponent condition={isPending}>
            <Skeleton
              variant="rectangular"
              sx={{ borderRadius: 0.5 }}
              height={20}
            />
            <Skeleton
              variant="rectangular"
              sx={{ borderRadius: 0.5 }}
              height={20}
            />
            <Skeleton
              variant="rectangular"
              sx={{ borderRadius: 0.5 }}
              height={20}
            />
          </ShowComponent>
          <ShowComponent condition={!isPending}>
            {organizations.map((org) => (
              <Box
                key={org.id}
                onClick={() => {
                  if (isSwitching || org.id === currentOrganizationId) return;
                  setOpen(false);
                  doSwitch(org.id);
                }}
                sx={{
                  px: 1,
                  py: 0.5,
                  cursor: isSwitching ? "wait" : "pointer",
                  borderRadius: 0.5,
                  backgroundColor:
                    org.id === currentOrganizationId
                      ? "background.neutral"
                      : "background.paper",
                  "&:hover": {
                    backgroundColor: "background.neutral",
                  },
                  opacity: isSwitching ? 0.6 : 1,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                }}
              >
                <Typography
                  typography="s2_1"
                  color="text.primary"
                  fontWeight={500}
                >
                  {org.display_name || org.name}
                </Typography>
                <ShowComponent condition={org.id === currentOrganizationId}>
                  <Iconify
                    icon="eva:checkmark-fill"
                    sx={{ width: 16, height: 16, color: "primary.main" }}
                  />
                </ShowComponent>
              </Box>
            ))}
          </ShowComponent>
        </Box>
        <Box>
          <Button
            variant="text"
            color="primary"
            fullWidth
            size="small"
            startIcon={<SVGColor src="/assets/icons/ic_add.svg" />}
            sx={{ justifyContent: "flex-start" }}
            onClick={() => {
              setOpen(false);
              useCreateOrganizationModal.getState().setOpen(true);
            }}
          >
            Create Organization
          </Button>
        </Box>
      </Box>
    );
  },
);

SelectOrganizationChild.displayName = "SelectOrganizationChild";

SelectOrganizationChild.propTypes = {
  setOpen: PropTypes.func,
};

const SelectOrganization = React.forwardRef(
  ({ open, anchorEl, setOpen }, popperTimeoutRef) => {
    return (
      <Popper
        open={open}
        anchorEl={anchorEl}
        placement="right-start"
        modifiers={[
          {
            name: "offset",
            options: {
              offset: [0, 8],
            },
          },
        ]}
        style={{ zIndex: 1300 }}
      >
        <SelectOrganizationChild setOpen={setOpen} ref={popperTimeoutRef} />
      </Popper>
    );
  },
);

SelectOrganization.displayName = "SelectOrganization";

SelectOrganization.propTypes = {
  open: PropTypes.bool,
  anchorEl: PropTypes.object,
  setOpen: PropTypes.func,
};

export default SelectOrganization;
