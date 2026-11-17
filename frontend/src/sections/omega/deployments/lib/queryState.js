/** Failed empty reads show an error; populated cached lists stay usable. */
export function errorWithoutData(query) {
  return (
    Boolean(query.error) &&
    (query.data == null ||
      (Array.isArray(query.data) && query.data.length === 0))
  );
}
