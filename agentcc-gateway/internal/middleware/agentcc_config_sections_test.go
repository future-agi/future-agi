package middleware

// The SDK's GatewayConfig.to_headers() nests its sections in x-agentcc-config
// ({"cache":{"ttl":300}}), and such a request is answered 200. The cache
// fields that have a header of their own are applied as that header; every
// other section, and every other cache field, is not read and is named in
// x-agentcc-config-ignored, so the caller can see what was skipped.

import (
	"fmt"
	"net/http"
	"strings"
	"testing"
)

func TestAgentccConfigHeaderReadsTheSDKNestedSections(t *testing.T) {
	cases := []struct {
		name, config string
		want         map[string]string // header -> value the handler must see
		absent       []string          // headers the config must not set
		ignored      string            // x-agentcc-config-ignored, "" for none
	}{
		{
			name:   "cache ttl only (the docs' per-request shape)",
			config: `{"cache":{"ttl":300}}`,
			want:   map[string]string{"x-agentcc-cache-ttl": "300"},
		},
		{
			name:   "cache force_refresh (concepts/configuration.mdx)",
			config: `{"cache":{"force_refresh":true}}`,
			want:   map[string]string{"x-agentcc-cache-force-refresh": "true"},
		},
		{
			name:   "cache forceRefresh (the TypeScript client's key)",
			config: `{"cache":{"forceRefresh":true}}`,
			want:   map[string]string{"x-agentcc-cache-force-refresh": "true"},
		},
		{
			name:    "the docs' client config: cache applied, strategy, retry and fallback named",
			config:  `{"cache":{"strategy":"exact","ttl":300,"namespace":"prod"},"retry":{"max_retries":3,"on_status_codes":[429,500,502,503]},"fallback":{"targets":[{"model":"gpt-4o-mini"}]}}`,
			want:    map[string]string{"x-agentcc-cache-ttl": "300", "x-agentcc-cache-namespace": "prod"},
			ignored: "cache.strategy,fallback,retry",
		},
		{
			name: "a dataclass serialized with every unset field null",
			config: `{"cache":{"strategy":null,"ttl":null,"namespace":null,"force_refresh":true},"retry":null,"fallback":null,` +
				`"load_balance":null,"guardrails":null,"routing":null,"mirror":null,"timeout":null}`,
			want:   map[string]string{"x-agentcc-cache-force-refresh": "true"},
			absent: []string{"x-agentcc-cache-ttl", "x-agentcc-cache-namespace", "x-agentcc-timeout"},
		},
		{
			name:    "a numeric timeout (its unit is not known): named, not applied",
			config:  `{"timeout":1234}`,
			absent:  []string{"x-agentcc-timeout", "x-agentcc-request-timeout"},
			ignored: "timeout",
		},
		{
			name:    "a nested timeout with cache and retry",
			config:  `{"timeout":{"total":1.234},"cache":{"ttl":300,"namespace":"prod"},"retry":{"max_retries":3}}`,
			want:    map[string]string{"x-agentcc-cache-ttl": "300", "x-agentcc-cache-namespace": "prod"},
			absent:  []string{"x-agentcc-timeout", "x-agentcc-request-timeout", "x-agentcc-max-attempts"},
			ignored: "retry,timeout",
		},
		{
			name:    "the other sections, by their documented names",
			config:  `{"load_balance":{"strategy":"round_robin"},"guardrails":{"input":["pii"]},"routing":{"rules":[]},"mirror":{"sample_rate":0.1}}`,
			ignored: "guardrails,load_balance,mirror,routing",
		},
		{
			name:    "loadBalance (the TypeScript client's key)",
			config:  `{"loadBalance":{"strategy":"round_robin"}}`,
			ignored: "loadBalance",
		},
		{
			name:   "an empty section or one of nulls asks for nothing",
			config: `{"retry":{},"fallback":{"targets":null},"cache":{}}`,
		},
		{
			name:   "a string timeout is still the x-agentcc-timeout duration",
			config: `{"timeout":"2s","cache":{"namespace":"prod"}}`,
			want:   map[string]string{"x-agentcc-timeout": "2s", "x-agentcc-cache-namespace": "prod"},
		},
		{
			name:   "flat keys and nested sections together",
			config: `{"request-timeout":1234,"property-env":"prod","cache":{"ttl":"5m"}}`,
			want:   map[string]string{"x-agentcc-request-timeout": "1234", "x-agentcc-cache-ttl": "5m"},
		},
		// A whole number written as a float (Python's ttl=300.0) or with an
		// exponent is a whole number of seconds.
		{
			name:   "cache ttl 300.0 (a Python float)",
			config: `{"cache":{"ttl":300.0}}`,
			want:   map[string]string{"x-agentcc-cache-ttl": "300"},
		},
		{
			name:   "cache ttl 3e2 (an exponent)",
			config: `{"cache":{"ttl":3e2}}`,
			want:   map[string]string{"x-agentcc-cache-ttl": "300"},
		},
		{
			name:   "cache ttl 0.3E+3 and 3000e-1 (a fraction digit and a negative exponent)",
			config: `{"cache":{"ttl":0.3E+3,"namespace":"prod"},"request-timeout":3000e-1}`,
			want:   map[string]string{"x-agentcc-cache-ttl": "300", "x-agentcc-request-timeout": "300", "x-agentcc-cache-namespace": "prod"},
		},
		{
			name:   "cache ttl 0.0 and -0.0 are zero",
			config: `{"cache":{"ttl":-0.0}}`,
			want:   map[string]string{"x-agentcc-cache-ttl": "0"},
		},
		{
			name:   "the flat keys take a whole float too",
			config: `{"cache-ttl":300.0,"request-timeout":5000.00}`,
			want:   map[string]string{"x-agentcc-cache-ttl": "300", "x-agentcc-request-timeout": "5000"},
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			w, seen := serveHeaders(t, map[string]string{"x-agentcc-config": tc.config})
			if w.Code != http.StatusOK || !seen.called {
				t.Fatalf("x-agentcc-config %s: status %d (handler ran %v), want 200; body %s", tc.config, w.Code, seen.called, w.Body.String())
			}
			for h, v := range tc.want {
				if got := seen.header.Get(h); got != v {
					t.Errorf("%s = %q, want %q from the nested config", h, got, v)
				}
			}
			for _, h := range tc.absent {
				if v, ok := seen.header[http.CanonicalHeaderKey(h)]; ok {
					t.Errorf("%s = %q, want it unset", h, v)
				}
			}
			got, sent := w.Header()["X-Agentcc-Config-Ignored"]
			switch {
			case tc.ignored == "" && sent:
				t.Errorf("x-agentcc-config-ignored = %q, want no header (nothing was skipped)", got)
			case tc.ignored != "" && (len(got) != 1 || got[0] != tc.ignored):
				t.Errorf("x-agentcc-config-ignored = %q, want %q", got, tc.ignored)
			}
		})
	}
}

func TestAgentccConfigHeaderNestedExplicitHeaderWins(t *testing.T) {
	_, seen := serveHeaders(t, map[string]string{
		"x-agentcc-config":    `{"cache":{"ttl":300,"namespace":"from-config"}}`,
		"x-agentcc-cache-ttl": "60",
	})
	if got := seen.header.Get("x-agentcc-cache-ttl"); got != "60" {
		t.Errorf("x-agentcc-cache-ttl = %q, want the explicit header's 60 (to_headers() sends both)", got)
	}
	if got := seen.header.Get("x-agentcc-cache-namespace"); got != "from-config" {
		t.Errorf("x-agentcc-cache-namespace = %q, want from-config", got)
	}
}

func TestAgentccConfigHeaderNestedRefusals(t *testing.T) {
	manyFields := make([]string, 0, 40)
	for i := 0; i < 40; i++ {
		manyFields = append(manyFields, fmt.Sprintf(`"f%d":1`, i))
	}
	cases := []struct {
		name, config, wantInMsg string
	}{
		{"a section that is a string", `{"cache":"yes"}`, `"cache"`},
		{"a section that is an array", `{"retry":[1,2]}`, `"retry"`},
		{"a section that is a bool", `{"guardrails":true}`, `"guardrails"`},
		{"timeout as a bool", `{"timeout":true}`, `"timeout"`},
		{"cache ttl negative", `{"cache":{"ttl":-1}}`, `"cache.ttl"`},
		{"cache ttl not a duration", `{"cache":{"ttl":"soon"}}`, `"cache.ttl"`},
		{"cache namespace not a string", `{"cache":{"namespace":5}}`, `"cache.namespace"`},
		{"cache force_refresh not a bool", `{"cache":{"force_refresh":"yes"}}`, `"cache.force_refresh"`},
		{"cache namespace with a control character", `{"cache":{"namespace":"a\r\nX-Injected: 1"}}`, `"cache.namespace"`},
		{"the same header twice: flat and nested", `{"cache-ttl":60,"cache":{"ttl":300}}`, `"cache.ttl"`},
		{"the same header twice: both spellings", `{"cache":{"force_refresh":true,"forceRefresh":true}}`, `"cache.forceRefresh"`},
		{"a cache field name with a control character", `{"cache":{"a\u0000b":1}}`, `"cache"`},
		{"a cache field name over 64 characters", `{"cache":{"` + strings.Repeat("f", 65) + `":1}}`, `"cache"`},
		{"more fields than the ignored header names", `{"cache":{` + strings.Join(manyFields, ",") + `}}`, "more than 32"},
		{"an undocumented section stays unknown", `{"timeouts":{"total":1234}}`, `"timeouts"`},
		// Only a number that is exactly whole is whole.
		{"cache ttl with a fraction", `{"cache":{"ttl":300.5}}`, `"cache.ttl"`},
		{"cache ttl with a fraction past float64 precision", `{"cache":{"ttl":3.0000000000000000001}}`, `"cache.ttl"`},
		{"cache ttl a negative float", `{"cache":{"ttl":-1.0}}`, `"cache.ttl"`},
		{"cache ttl a negative exponent that leaves a fraction", `{"cache":{"ttl":3e-1}}`, `"cache.ttl"`},
		{"cache ttl past int64", `{"cache":{"ttl":1e30}}`, `"cache.ttl"`},
		{"cache ttl past int64 by one", `{"cache":{"ttl":9223372036854775808.0}}`, `"cache.ttl"`},
		{"cache ttl with an exponent past int", `{"cache":{"ttl":1e99999999999999999999}}`, `"cache.ttl"`},
		{"cache ttl a string float stays refused", `{"cache":{"ttl":"300.0"}}`, `"cache.ttl"`},
		{"request-timeout zero as a float", `{"request-timeout":0.0}`, `"request-timeout"`},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			w, seen := serveHeaders(t, map[string]string{"x-agentcc-config": tc.config})
			if seen.called || w.Code != http.StatusBadRequest {
				t.Fatalf("x-agentcc-config %s: status %d (handler ran %v), want 400; body %s", tc.config, w.Code, seen.called, w.Body.String())
			}
			e := errorOf(t, w)
			if e.Code != "invalid_agentcc_config" || e.Param == nil || *e.Param != "x-agentcc-config" {
				t.Errorf("error code %q param %v, want invalid_agentcc_config naming x-agentcc-config", e.Code, e.Param)
			}
			if !strings.Contains(e.Message, tc.wantInMsg) {
				t.Errorf("message %q does not name %s", e.Message, tc.wantInMsg)
			}
			if v, ok := w.Header()["X-Agentcc-Config-Ignored"]; ok {
				t.Errorf("a refusal carries x-agentcc-config-ignored %q", v)
			}
		})
	}
}

// The response header that names what was skipped is one browsers may read.
func TestAgentccConfigIgnoredIsExposedToBrowsers(t *testing.T) {
	if expose := corsPreflight(t, "").Get("Access-Control-Expose-Headers"); !listsHeader(expose, "x-agentcc-config-ignored") {
		t.Errorf("Access-Control-Expose-Headers %q lacks x-agentcc-config-ignored", expose)
	}
}

// exactWholeNumber reads the number's digits, so it is exact where a float64
// would round, and bounded where a big number built from the exponent would
// not be.
func TestExactWholeNumber(t *testing.T) {
	cases := []struct {
		in   string
		want int64
		ok   bool
	}{
		{"300.0", 300, true},
		{"3e2", 300, true},
		{"3E+2", 300, true},
		{"0.3e3", 300, true},
		{"3000e-1", 300, true},
		{"100e-2", 1, true},
		{"0.0", 0, true},
		{"-0.0", 0, true},
		{"0e5", 0, true},
		{"-12.000", -12, true},
		{"9223372036854775807.0", 9223372036854775807, true},
		{"9.223372036854775807e18", 9223372036854775807, true},
		{"-9223372036854775808.0", -9223372036854775808, true},
		{"0.000000000000000000000000000001e30", 1, true},
		{"300.5", 0, false},
		{"3.0000000000000000001", 0, false},
		{"1e-1", 0, false},
		{"1e19", 0, false},
		{"9223372036854775808.0", 0, false},
		{"-9223372036854775809.0", 0, false},
		{"1e1048577", 0, false},
		{"1e-1048577", 0, false},
		{"1e9223372036854775807", 0, false},
		{"1e99999999999999999999", 0, false},
	}
	for _, tc := range cases {
		n, ok := exactWholeNumber(tc.in)
		if ok != tc.ok || n != tc.want {
			t.Errorf("exactWholeNumber(%q) = %d, %v; want %d, %v", tc.in, n, ok, tc.want, tc.ok)
		}
	}
}
