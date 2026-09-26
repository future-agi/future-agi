// Node 23+ ships a global `localStorage` that conflicts with jsdom's.
// This tiny side-effect module replaces it before the component graph loads.
const store = {};
const api = {
  getItem(key) {
    return store[key] ?? null;
  },
  setItem(key, value) {
    store[key] = String(value);
  },
  removeItem(key) {
    delete store[key];
  },
  clear() {
    for (const k of Object.keys(store)) delete store[k];
  },
  get length() {
    return Object.keys(store).length;
  },
  key(index) {
    return Object.keys(store)[index] ?? null;
  },
};
Object.defineProperty(globalThis, "localStorage", {
  value: api,
  writable: true,
  configurable: true,
});
