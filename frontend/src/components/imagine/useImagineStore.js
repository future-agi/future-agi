import { create } from "src/utils/zustand";

const useImagineStore = create((set, get) => ({
  widgets: [],
  isLoading: false,
  conversationId: null,
  _savedViewId: null,

  // Analysis cache: { "traceId::widgetId": { content, timestamp } }
  // Keyed by trace+widget so each trace gets its own analysis
  analysisCache: {},

  addWidget: (widget) =>
    set((s) => {
      const idx = s.widgets.findIndex((w) => w.id === widget.id);
      if (idx >= 0) {
        const next = [...s.widgets];
        next[idx] = widget;
        return { widgets: next };
      }
      return { widgets: [...s.widgets, widget] };
    }),

  updateWidget: (id, updates) =>
    set((s) => ({
      widgets: s.widgets.map((w) => (w.id === id ? { ...w, ...updates } : w)),
    })),

  removeWidget: (id) =>
    set((s) => ({
      widgets: s.widgets.filter((w) => w.id !== id),
    })),

  replaceAll: (widgets) => set({ widgets }),

  clearWidgets: () => set({ widgets: [] }),

  setConversationId: (conversationId) => set({ conversationId }),

  setLoading: (isLoading) => set({ isLoading }),

  // Analysis cache methods
  getAnalysis: (traceId, widgetId) => {
    const key = `${traceId}::${widgetId}`;
    return get().analysisCache[key]?.content || null;
  },

  setAnalysis: (traceId, widgetId, content) =>
    set((s) => {
      const key = `${traceId}::${widgetId}`;
      if (content === null) {
        // Clear cache entry (for rerun)
        const next = { ...s.analysisCache };
        delete next[key];
        return { analysisCache: next };
      }
      return {
        analysisCache: {
          ...s.analysisCache,
          [key]: { content, timestamp: Date.now() },
        },
      };
    }),

  setSavedViewId: (id) => set({ _savedViewId: id }),

  reset: () =>
    set({
      widgets: [],
      isLoading: false,
      conversationId: null,
      analysisCache: {},
      _savedViewId: null,
    }),
}));

export default useImagineStore;
