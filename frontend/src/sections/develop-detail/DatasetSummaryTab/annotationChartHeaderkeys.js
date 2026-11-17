export const numericGraphHeader = [
  {
    text: "Pearson correlation",
    valueKey: "correlation",
    missing: "Needs 2+ reviewers on the same rows",
  },
  { text: "Mean", valueKey: "avgValue" },
  { text: "Std. deviation", valueKey: "stddevValue" },
  { text: "Range", valueKey: "range" },
];

export const categoricalGraphHeader = [
  {
    text: "Fleiss's kappa",
    valueKey: "kappa",
    missing: "Needs 2+ reviewers on the same rows",
  },
  { text: "Most frequent", valueKey: "modeValue" },
  { text: "Unique labels", valueKey: "numUnique" },
  { text: "Label coverage", valueKey: "labelCoverage" },
];

export const textGraphHeader = [
  {
    text: "Cosine Similarity (Semantic alignment)",
    valueKey: "cosineSimilarity",
  },
  { text: "Avg.", valueKey: "vocabSize" },
  { text: "Vocabulary size", valueKey: "vocabSize" },
  { text: "Length Range", valueKey: "lenRange" },
];

export const thumbsGraphHeader = [
  { text: "Approval rate", valueKey: "" },
  { text: "Rejection rate", valueKey: "" },
  { text: "Sentiment", valueKey: "" },
  { text: "Label coverage ", valueKey: "" },
];

export const starGraphHeader = [
  { text: "Avg. rating", valueKey: "avgValue" },
  { text: "Satisfaction rate (4-5 stars)", valueKey: "satisfactionRate" },
  { text: "Most common", valueKey: "modeValue" },
  { text: "Label coverage", valueKey: "labelCoverage" },
];
