// Vel's evaluation change log (his `evalChanges`: each change with the eval
// version it made), narrowed to one eval. Shown only with
// ?uiSample=evalHistory: our API keeps no history per evaluation yet.
export const sampleEvalHistory = (name) => [
  {
    label: "e3",
    detail: `Edited ${name}`,
    note: "Judge model changed",
    ago: "2d ago",
  },
  {
    label: "e2",
    detail: `Rolled back to environment v2's evaluations`,
    note: "Mapping restored",
    ago: "9d ago",
  },
  { label: "e1", detail: `Added ${name}`, ago: "24d ago" },
];
