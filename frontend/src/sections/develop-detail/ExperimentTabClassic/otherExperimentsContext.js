import { createContext } from "react";

// What the dataset's Experiments tab knows about the experiments that open in
// the original results view, for the runs view it wraps:
//   count: how many there are; null until their read has answered.
//   pending: true while that first read is in flight.
//   group: the node that lists them, rendered under the runs view; null when
//     the tab has no such group.
//   byId: their list rows by experiment id, so a run row of one of them can
//     say which experiment it belongs to and link to it.
// With no provider the runs view behaves exactly as it always has: nothing is
// listed under it and its empty state reads as before.
export const OtherExperimentsContext = createContext({
  count: 0,
  pending: false,
  group: null,
  byId: {},
});
