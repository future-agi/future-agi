// A select in a gateway filter bar, at the height of the FA search field next
// to it (GatewayFilterBar sets the search field to 30 px). The values name
// themselves ("All rules", "On"), so there is no floating label; the select
// carries an aria-label instead.
export const FILTER_SELECT = {
  "& .MuiOutlinedInput-root": { height: "30px" },
  "& .MuiSelect-select": { py: 0, typography: "s2" },
};
