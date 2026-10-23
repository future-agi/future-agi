package guardrails

import (
	"sort"
	"strings"
)

// EnvReferenceField returns "config.<key>" for the first of cfg's top-level
// string values that holds an environment reference ("${"), or "" if none
// does. keys names the settings to read, in that order; with no keys it reads
// every top-level key, sorted, so the reported field is deterministic.
//
// The futureagi and external guardrails resolve a whole-string ${NAME}
// against the gateway process environment only in top-level string settings:
// futureagi's api_key, secret_key and base_url, and every setting an external
// adapter reads through getStringConfig. That is for operator config.yaml
// rules (NewConfigFileRule). A rule built from org-pushed config (New) uses
// this to refuse a "${" in exactly those strings instead, so org config
// never reads the gateway's environment. Nested values, list items and map
// keys are never resolved, so a
// "${" there is plain text: a Gray Swan rule text or an Enkrypt keyword may
// hold one, and the rule runs. Django refuses the same strings on save
// (agentcc/validators.py). The field is named, never the value, so an error
// built from it never echoes a secret.
func EnvReferenceField(cfg map[string]interface{}, keys ...string) string {
	if len(keys) == 0 {
		keys = make([]string, 0, len(cfg))
		for k := range cfg {
			keys = append(keys, k)
		}
		sort.Strings(keys)
	}
	for _, k := range keys {
		if s, ok := cfg[k].(string); ok && strings.Contains(s, "${") {
			return "config." + k
		}
	}
	return ""
}
