// How a scenario's persona is summed up wherever it shows: the table's
// Persona column, the scenario drawer and the list view's persona facts.

export const genderLabel = (gender) =>
  gender ? gender.charAt(0).toUpperCase() + gender.slice(1) : null;

// Gender and age group, or the persona's role when it has neither (the
// requester personas on browser and API templates).
export const personaSubline = (persona) =>
  [
    genderLabel(persona?.gender),
    persona?.ageGroup,
    !persona?.gender && !persona?.ageGroup && persona?.role,
  ]
    .filter(Boolean)
    .join(" · ");
