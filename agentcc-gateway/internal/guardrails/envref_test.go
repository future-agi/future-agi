package guardrails

import "testing"

// EnvReferenceField names the first top-level string setting holding a "${",
// deterministically, and never returns a value. It reads only the strings
// a config.yaml futureagi or external rule resolves against the
// environment: top-level string values, all of them or the keys given. A
// nested value, a list item or a map key is plain text.
func TestEnvReferenceField(t *testing.T) {
	cases := []struct {
		name string
		cfg  map[string]interface{}
		keys []string
		want string
	}{
		{"whole-string reference", map[string]interface{}{"api_key": "${X}"}, nil, "config.api_key"},
		{"embedded reference", map[string]interface{}{"base_url": "https://h/${X}/p"}, nil, "config.base_url"},
		{"first field in sorted order", map[string]interface{}{"secret_key": "${Y}", "api_key": "${X}"}, nil, "config.api_key"},
		{"keys in the order given", map[string]interface{}{"secret_key": "${Y}", "api_key": "${X}"}, []string{"secret_key", "api_key"}, "config.secret_key"},
		{"a key not listed is not read", map[string]interface{}{"call_type": "${X}", "api_key": "k"}, []string{"api_key", "base_url", "secret_key"}, ""},
		{"nested map value is plain text", map[string]interface{}{"rules": map[string]interface{}{"r1": "holds ${jndi:"}}, nil, ""},
		{"string map value is plain text", map[string]interface{}{"headers": map[string]string{"X-Key": "${T}"}}, nil, ""},
		{"list item is plain text", map[string]interface{}{"categories": []interface{}{"ok", "${Z}"}}, nil, ""},
		{"string list item is plain text", map[string]interface{}{"eval_ids": []string{"${Z}"}}, nil, ""},
		{"a map key is not a value", map[string]interface{}{"${K}": "v"}, nil, ""},
		{"clean config", map[string]interface{}{"api_key": "literal", "eval_id": "e", "retry": float64(1), "x": nil, "on": true}, nil, ""},
		{"a lone $ or { is not a reference", map[string]interface{}{"api_key": "a$b{c}"}, nil, ""},
		{"nil", nil, nil, ""},
		{"nil with keys", nil, []string{"api_key"}, ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := EnvReferenceField(tc.cfg, tc.keys...); got != tc.want {
				t.Fatalf("EnvReferenceField = %q, want %q", got, tc.want)
			}
		})
	}
}
