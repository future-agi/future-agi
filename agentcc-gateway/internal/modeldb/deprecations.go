package modeldb

import (
	"encoding/json"
	"fmt"
	"strings"
	"time"
)

// Deprecation is one model's scheduled shutdown: from ShutdownDate on, the
// model does not serve, and a request for it is refused with 410
// model_expired, naming Replacement.
type Deprecation struct {
	// Key is the calendar entry: the vendor's model id, or
	// "<host>/<model id>" for a host that retires it on another day.
	Key          string
	ShutdownDate string // "2026-10-23"
	Replacement  string
	Source       string
	shutdown     time.Time
}

// Reason says why a request for the model is refused.
func (d Deprecation) Reason() string {
	return fmt.Sprintf("model %s was shut down on %s; use %s instead", d.Key, d.ShutdownDate, d.Replacement)
}

// Deprecations is the model shutdown calendar, catalog/deprecations.json.
// The zero value and nil are empty calendars.
type Deprecations struct {
	byKey map[string]Deprecation
	// byHost indexes the host entries by host, then bare model id, so a
	// lookup on the request path builds no string (it runs for every
	// eligibility check).
	byHost map[string]map[string]Deprecation
}

// ParseDeprecations reads a calendar in the catalog/deprecations.json shape.
// Every entry needs a model id, a shutdown date (YYYY-MM-DD) and a
// replacement.
func ParseDeprecations(data []byte) (*Deprecations, error) {
	var file struct {
		Models map[string]struct {
			ShutdownDate string `json:"shutdown_date"`
			Replacement  string `json:"replacement"`
			Source       string `json:"source"`
		} `json:"models"`
	}
	if err := json.Unmarshal(data, &file); err != nil {
		return nil, fmt.Errorf("deprecations: %w", err)
	}
	cal := &Deprecations{byKey: make(map[string]Deprecation, len(file.Models)), byHost: map[string]map[string]Deprecation{}}
	for key, e := range file.Models {
		if strings.TrimSpace(key) == "" || strings.HasSuffix(key, "/") {
			return nil, fmt.Errorf("deprecations: an entry has no model id (%q)", key)
		}
		day, err := time.Parse(time.DateOnly, e.ShutdownDate)
		if err != nil {
			return nil, fmt.Errorf("deprecations: %s: shutdown_date %q is not YYYY-MM-DD", key, e.ShutdownDate)
		}
		if strings.TrimSpace(e.Replacement) == "" {
			return nil, fmt.Errorf("deprecations: %s: no replacement", key)
		}
		d := Deprecation{Key: key, ShutdownDate: e.ShutdownDate, Replacement: e.Replacement, Source: e.Source, shutdown: day}
		cal.byKey[key] = d
		if host, bare, ok := strings.Cut(key, "/"); ok {
			if cal.byHost[host] == nil {
				cal.byHost[host] = map[string]Deprecation{}
			}
			cal.byHost[host][bare] = d
		}
	}
	return cal, nil
}

// Len is the number of calendar entries.
func (c *Deprecations) Len() int {
	if c == nil {
		return 0
	}
	return len(c.byKey)
}

// DeprecationHost is the host a calendar lookup takes for a provider: its
// api_format ("openai", "azure", "gemini", ...), or "vertex" for a Gemini-
// format provider on Vertex AI (a base URL on aiplatform.googleapis.com, as
// the Gemini adapter decides). It is never the provider's gateway ID, which
// an operator or org names freely ("azure-eastus"). "google" is the
// registry's other name for the Gemini adapter (providers.createProvider),
// so it is read as "gemini".
func DeprecationHost(apiFormat, baseURL string) string {
	format := strings.ToLower(strings.TrimSpace(apiFormat))
	if format == "google" {
		format = "gemini"
	}
	if format == "gemini" && strings.Contains(baseURL, "aiplatform.googleapis.com") {
		return "vertex"
	}
	return format
}

// Lookup returns model's scheduled shutdown as host serves it. host is the
// provider's type (DeprecationHost), not its gateway ID. model may carry a
// vendor or host prefix ("openai/gpt-image-1"). A host entry
// ("azure/gpt-image-1-mini" for host "azure") wins, then the vendor's entry
// for the bare id. Only when host is unknown ("") does the model's own prefix
// stand in for it: in the gateway a model prefix names a provider ID or a
// vendor, never the serving host's type, so with a known host it would match
// another host's entry ("gemini/imagen-4.0-generate-001" on Vertex AI, which
// still serves it).
func (c *Deprecations) Lookup(host, model string) (Deprecation, bool) {
	if c == nil || model == "" {
		return Deprecation{}, false
	}
	bare := model[strings.LastIndexByte(model, '/')+1:]
	if host == "" {
		host, _, _ = strings.Cut(model, "/") // the model's own prefix, if any
	}
	if d, ok := c.byHost[host][bare]; ok && host != model {
		return d, true
	}
	d, ok := c.byKey[bare]
	return d, ok
}

// ShutDown reports whether model, as host (the provider's type,
// DeprecationHost) serves it, is past its shutdown date at now (UTC days:
// shut down from the start of the date), and returns its calendar entry
// when it is.
func (c *Deprecations) ShutDown(host, model string, now time.Time) (Deprecation, bool) {
	d, ok := c.Lookup(host, model)
	if !ok || now.UTC().Before(d.shutdown) {
		return Deprecation{}, false
	}
	return d, true
}
