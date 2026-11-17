import { Box, Button, ButtonBase, Skeleton, Typography } from "@mui/material";
import SwitcherSubmenu from "./SwitcherSubmenu";
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
import { isPrototypeMode } from "src/utils/prototype";
import { ORG_CREATE_SSO_REASON } from "./orgCreateReason";

const SelectOrganizationChild = React.forwardRef(
  ({ setOpen, canCreateOrganization }, popperTimeoutRef) => {
    const { currentOrganizationId, switchOrganization } = useOrganization();

    const { data, isPending, isError, isFetching, refetch } = useQuery({
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
        onMouseLeave={(event) => {
          if (event.currentTarget.contains(document.activeElement)) return;
          popperTimeoutRef.current = setTimeout(() => {
            setOpen(false);
          }, 100);
        }}
        sx={{
          backgroundColor: "background.paper",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          boxShadow: (theme) => theme.customShadows?.dropdown,
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
              sx={{ borderRadius: "4px" }}
              height={20}
            />
            <Skeleton
              variant="rectangular"
              sx={{ borderRadius: "4px" }}
              height={20}
            />
            <Skeleton
              variant="rectangular"
              sx={{ borderRadius: "4px" }}
              height={20}
            />
          </ShowComponent>
          {isError && (
            <Box role="alert" sx={{ px: 1 }}>
              <Typography typography="s2_1" color="text.secondary">
                {organizations.length
                  ? "Could not refresh organizations. Showing the last loaded list."
                  : "Could not load organizations"}
              </Typography>
              <Button
                size="small"
                disabled={isFetching}
                onClick={() => refetch()}
              >
                {isFetching ? "Retrying…" : "Retry"}
              </Button>
            </Box>
          )}
          {!isPending && !isError && organizations.length === 0 && (
            <Typography typography="s2_1" color="text.secondary" sx={{ px: 1 }}>
              No organizations available
            </Typography>
          )}
          <ShowComponent condition={!isPending}>
            {organizations.map((org) => (
              <ButtonBase
                key={org.id}
                disabled={isSwitching}
                aria-pressed={org.id === currentOrganizationId}
                onClick={() => {
                  if (isSwitching || org.id === currentOrganizationId) return;
                  setOpen(false);
                  doSwitch(org.id);
                }}
                sx={{
                  px: 1,
                  py: 0.5,
                  cursor: isSwitching ? "wait" : "pointer",
                  borderRadius: "4px",
                  backgroundColor:
                    org.id === currentOrganizationId
                      ? "background.neutral"
                      : "background.paper",
                  "&:hover, &:focus-visible": {
                    backgroundColor: "background.neutral",
                  },
                  "&:focus-visible": { outline: "2px solid", outlineColor: "primary.main" },
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
                    icon="solar:check-linear"
                    sx={{ width: 16, height: 16, color: "primary.main" }}
                  />
                </ShowComponent>
              </ButtonBase>
            ))}
          </ShowComponent>
        </Box>
        {/* L7b absent-sweep: not mocked, so absent in prototype builds. */}
        {!isPrototypeMode() && (
          <Box>
            <Button
              variant="text"
              color="primary"
              fullWidth
              size="small"
              startIcon={<SVGColor src="/assets/icons/ic_add.svg" />}
              sx={{ justifyContent: "flex-start" }}
              disabled={!canCreateOrganization}
              onClick={() => {
                setOpen(false);
                useCreateOrganizationModal.getState().setOpen(true);
              }}
            >
              Create Organization
            </Button>
            {!canCreateOrganization && (
              <Typography
                variant="caption"
                component="p"
                sx={{ px: 1, pb: 0.5, color: "text.secondary" }}
              >
                {ORG_CREATE_SSO_REASON}
              </Typography>
            )}
          </Box>
        )}
      </Box>
    );
  },
);

SelectOrganizationChild.displayName = "SelectOrganizationChild";

SelectOrganizationChild.propTypes = {
  setOpen: PropTypes.func,
  canCreateOrganization: PropTypes.bool,
};

const SelectOrganization = React.forwardRef(
  (
    { open, anchorEl, setOpen, autoFocus, canCreateOrganization = true },
    popperTimeoutRef,
  ) => (
    <SwitcherSubmenu open={open} anchorEl={anchorEl} setOpen={setOpen} autoFocus={autoFocus} label="Organizations">
      {(changeOpen) => (
        <SelectOrganizationChild
          setOpen={changeOpen}
          canCreateOrganization={canCreateOrganization}
          ref={popperTimeoutRef}
        />
      )}
    </SwitcherSubmenu>
  ),
);

SelectOrganization.displayName = "SelectOrganization";

SelectOrganization.propTypes = {
  autoFocus: PropTypes.bool,
  canCreateOrganization: PropTypes.bool,
  open: PropTypes.bool,
  anchorEl: PropTypes.object,
  setOpen: PropTypes.func,
};

export default SelectOrganization;
