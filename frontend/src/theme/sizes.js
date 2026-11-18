import { dsSize } from "./ds-tokens";

// One table row height for the whole product, from the design-system tokens.
// Every grid and table reads these instead of typing its own number:
// default for a single-line row, twoLine for a name with a line under it.
export const ROW_HEIGHT = dsSize.row;
export const TABLE_HEADER_HEIGHT = dsSize.tableHeader;
