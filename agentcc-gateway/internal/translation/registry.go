package translation

// inbound is the package-level registry of InboundTranslator implementations,
// keyed by api_format string (e.g. "anthropic", "google").
var inbound = map[string]InboundTranslator{}

// Register adds a translator to the registry. Called from init() of each
// translator package — mirrors how providers register themselves.
func Register(t InboundTranslator) { inbound[t.Name()] = t }

// InboundFor returns the registered translator for the given api_format.
// Returns false if no translator is registered for that format.
func InboundFor(apiFormat string) (InboundTranslator, bool) {
	t, ok := inbound[apiFormat]
	return t, ok
}

// All returns the names of all registered translators — for debug/logging.
func All() []string {
	names := make([]string, 0, len(inbound))
	for name := range inbound {
		names = append(names, name)
	}
	return names
}
