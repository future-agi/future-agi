import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";

export default function QueueItemsEmpty({ onAddClick }) {
  return (
    <Stack
      alignItems="center"
      justifyContent="center"
      sx={{ py: 10, textAlign: "center" }}
    >
      <Box
        sx={{
          width: 48,
          height: 48,
          borderRadius: 0.5,
          border: "2px solid",
          borderColor: "divider",

          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          mb: 2,
        }}
      >
        <SvgColor
          src="/assets/icons/ic_annotation_v2.svg"
          sx={{
            background: "linear-gradient(135deg, #7857FC 0%, #CF6BE8 100%)",
          }}
        />
      </Box>
      <Typography variant="h6" gutterBottom>
        No items in this queue
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        {onAddClick
          ? "Add items from datasets, traces, prototypes, or simulations."
          : "A queue manager can add items to this queue."}
      </Typography>
      {onAddClick && (
        <Button
          variant="contained"
          color="primary"
          startIcon={<Iconify icon="mingcute:add-line" />}
          onClick={onAddClick}
        >
          Add Items
        </Button>
      )}
    </Stack>
  );
}

QueueItemsEmpty.propTypes = {
  onAddClick: PropTypes.func,
};
