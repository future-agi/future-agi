// Vel's run pre-check samples for a voice environment, copy without em
// dashes. Shown only with ?uiSample=usage (the budget line) or
// ?uiSample=usageheld (the refusal): there is no pre-run usage check yet.
export const SAMPLE_USAGE_NOTICE = {
  line: "Your org has used 4,100 of 5,000 voice-simulation minutes this month (October). This run adds about 410, taking it to 90%, past your 90% budget line. It still runs.",
};

export const SAMPLE_USAGE_REFUSAL = {
  title: "Over your Voice simulation limit",
  line: "4,100 of 5,000 minutes used this October; this run needs about 1,200 minutes.",
};
