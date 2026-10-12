// Import this BEFORE any module that touches localStorage at load time. The agents helper barrel
// transitively imports develop-detail/states.jsx, which reads localStorage at module eval — and the
// test runtime's localStorage isn't a usable Storage. Provide a minimal in-memory one so importing
// the schema factory doesn't crash. (Not a test file — the leading underscore keeps it out of globs.)
if (typeof globalThis.localStorage === "undefined" ||
    typeof globalThis.localStorage.getItem !== "function") {
  const store = new Map();
  globalThis.localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
    clear: () => store.clear(),
    key: (i) => Array.from(store.keys())[i] ?? null,
    get length() {
      return store.size;
    },
  };
}
