import React from "react";
import PropTypes from "prop-types";
import Button from "@mui/material/Button";
import { useIsFetching, useQueryClient } from "@tanstack/react-query";
import { Navigate } from "react-router-dom";

import { LoadingScreen } from "src/components/loading-screen";
import { CAPABILITIES_QUERY_KEY } from "src/hooks/useCapabilities";

import OmegaEmptyState from "./OmegaEmptyState";
import { OMEGA_PRODUCTS, PRODUCT_LABEL, PRODUCT_STATUS } from "./rollout";
import { useOmegaProductStatus } from "./useOmegaProducts";

// The route-level rollout gate (READINESS 3.13, RC-01). No route of a product
// renders unless the server admits this org and workspace:
//   - not admitted: Future AGI's standard 404, exactly as for an unknown URL,
//     so a dark product does not reveal itself;
//   - paused: Future AGI's own paused copy (never a 404, so the product does
//     not seem to vanish for its users);
//   - the answer cannot be read: a retry state, and still nothing renders;
//   - while loading: the host's loading screen, never the product.

const ICONS = {
  console: "console",
  sandboxes: "sandboxes",
  deployments: "deployments",
  studio_tab: "studio",
  hub: "hub",
};

export function ProductPausedView({ product }) {
  return (
    <OmegaEmptyState
      icon={ICONS[product]}
      title={`${PRODUCT_LABEL[product]} is paused`}
      description="This product is paused. Try again later."
    />
  );
}

ProductPausedView.propTypes = {
  product: PropTypes.oneOf(OMEGA_PRODUCTS).isRequired,
};

// No query observer of its own: one would refetch on mount, flip the gate back
// to loading, unmount this view and loop. It only asks again when clicked.
function ProductUnavailableView({ product }) {
  const queryClient = useQueryClient();
  const isFetching = useIsFetching({ queryKey: CAPABILITIES_QUERY_KEY }) > 0;
  const refetch = () =>
    queryClient.refetchQueries({ queryKey: CAPABILITIES_QUERY_KEY });
  return (
    <OmegaEmptyState
      icon={ICONS[product]}
      title={`${PRODUCT_LABEL[product]} can't be opened right now`}
      description={
        product === "console"
          ? "Console access could not be confirmed. Try again."
          : "Future AGI couldn't confirm access just now. Try again in a few seconds."
      }
      action={
        <Button
          variant="outlined"
          size="small"
          disabled={isFetching}
          onClick={() => refetch()}
        >
          Try again
        </Button>
      }
    />
  );
}

ProductUnavailableView.propTypes = {
  product: PropTypes.oneOf(OMEGA_PRODUCTS).isRequired,
};

export default function ProductGate({ product, children }) {
  const status = useOmegaProductStatus(product);
  if (status === PRODUCT_STATUS.ADMITTED) return children;
  if (status === PRODUCT_STATUS.LOADING)
    return <LoadingScreen sx={undefined} />;
  if (status === PRODUCT_STATUS.PAUSED) {
    return <ProductPausedView product={product} />;
  }
  if (status === PRODUCT_STATUS.UNAVAILABLE) {
    return <ProductUnavailableView product={product} />;
  }
  return <Navigate to="/404" replace />;
}

ProductGate.propTypes = {
  product: PropTypes.oneOf(OMEGA_PRODUCTS).isRequired,
  children: PropTypes.node,
};
