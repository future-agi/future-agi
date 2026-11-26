package validation_test

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"regexp"
	"strconv"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/contentmod"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/language"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/pii"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/secrets"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/topic"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/validation"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

type selectedConfigValidator interface {
	ValidateConfig() error
}

// The assertion is deliberately runtime-only: these regressions compile against
// the original constructors, and fail because selected validation is missing.
func configError(t *testing.T, g guardrails.Guardrail) error {
	t.Helper()
	v, ok := g.(selectedConfigValidator)
	if !ok {
		t.Fatalf("%s does not expose selected configuration validation", g.Name())
	}
	return v.ValidateConfig()
}

func selectedInput(text string) *guardrails.CheckInput {
	raw, _ := json.Marshal(text)
	return &guardrails.CheckInput{Request: &models.ChatCompletionRequest{
		Messages: []models.Message{{Role: "user", Content: raw}},
	}}
}

func selectedCheck(t *testing.T, g guardrails.Guardrail, text string, wantPass bool) {
	t.Helper()
	result := g.Check(context.Background(), selectedInput(text))
	if result == nil || result.Pass != wantPass {
		t.Fatalf("%s Check pass = %v, want %v", g.Name(), result, wantPass)
	}
}

type selectorFamily struct {
	name, field, sample string
	new                 func(map[string]interface{}) guardrails.Guardrail
	// Pin the names to the real detector catalogs, with executable examples.
	examples map[string]string
}

func selectorFamilies() []selectorFamily {
	return []selectorFamily{
		{"pii", "entities", "email", func(c map[string]interface{}) guardrails.Guardrail { return pii.New(c) }, map[string]string{
			"email": "person@example.test", "phone": "555-123-4567", "ssn": "123-45-6789",
			"credit_card": "4111 1111 1111 1111", "ipv4": "192.168.1.1", "ipv6": "2001:0db8:0000:0000:0000:0000:0000:0001",
			"dob": "2000-01-01", "passport": "passport: 123456789", "drivers_license": "DL: A1234567",
			"iban": "GB82WEST12345698765432", "zip_code": "12345-6789", "aws_key": "AKIA1234567890ABCDEF",
			"api_key": "key_abcdefghijklmnopqrst", "url_credentials": "https://example:fixture@example.test",
			"mac_address": "aa:bb:cc:dd:ee:ff", "ein": "12-3456789", "mrn": "MRN123456", "bitcoin": "1BoatSLRHtKNngkdXEeobR76b53LETtpyT",
		}},
		{"contentmod", "categories", "toxicity", func(c map[string]interface{}) guardrails.Guardrail { return contentmod.New(c) }, map[string]string{
			"toxicity": "you are stupid", "hate_speech": "inferior race", "violence": "i will kill you",
			"sexual": "explicit sexual content", "self_harm": "how to kill myself",
		}},
		{"secrets", "types", "private_key", func(c map[string]interface{}) guardrails.Guardrail { return secrets.New(c) }, map[string]string{
			"aws_access_key": "AKIA1234567890ABCDEF", "aws_secret_key": "secret_key=" + strings.Repeat("a", 40),
			"github_token": "ghp_" + strings.Repeat("a", 36), "gitlab_token": "glpat-" + strings.Repeat("a", 20),
			"slack_token": "xoxb-fixture", "stripe_key": "sk_test_" + strings.Repeat("a", 20),
			"openai_key": "sk-" + strings.Repeat("a", 20), "private_key": "-----BEGIN PRIVATE KEY-----",
			"connection_string": "postgres://example:fixture@example.test", "jwt": "eyJ" + strings.Repeat("a", 10) + "." + strings.Repeat("b", 10) + "." + strings.Repeat("c", 10),
			"password_assign": "password=fixture_only",
		}},
	}
}

func TestSelectedConfigSelectorCatalogs(t *testing.T) {
	for _, family := range selectorFamilies() {
		t.Run(family.name, func(t *testing.T) {
			for name, text := range family.examples {
				t.Run(name, func(t *testing.T) {
					g := family.new(map[string]interface{}{family.field: []interface{}{name}})
					if err := configError(t, g); err != nil {
						t.Fatal(err)
					}
					selectedCheck(t, g, text, false)
				})
			}
			for i, cfg := range []map[string]interface{}{nil, {}, {family.field: []interface{}{}}} {
				t.Run(fmt.Sprintf("default_%d", i), func(t *testing.T) {
					g := family.new(cfg)
					if err := configError(t, g); err != nil {
						t.Fatal(err)
					}
					selectedCheck(t, g, family.examples[family.sample], false)
				})
			}
		})
	}
}

func TestSelectedConfigMalformedSelectors(t *testing.T) {
	for _, family := range selectorFamilies() {
		t.Run(family.name, func(t *testing.T) {
			for name, value := range map[string]interface{}{
				"null": nil, "scalar": family.sample, "object": map[string]interface{}{},
				"wrong_element": []interface{}{42}, "mixed_element": []interface{}{family.sample, 42},
				"unknown": []interface{}{"private-selector-sentinel"}, "mixed_unknown": []interface{}{family.sample, "private-selector-sentinel"},
				"blank": []interface{}{""}, "case_mismatch": []interface{}{strings.ToUpper(family.sample)},
				"typed_slice_ignored_by_constructor": []string{family.sample},
			} {
				t.Run(name, func(t *testing.T) {
					g := family.new(map[string]interface{}{family.field: value})
					err := configError(t, g)
					if err == nil {
						t.Fatal("malformed selector accepted")
					}
					if strings.Contains(err.Error(), "private-selector-sentinel") {
						t.Fatal("raw selector leaked")
					}
				})
			}
		})
	}
}

func TestSelectedConfigCapturesOriginalSelectors(t *testing.T) {
	for _, family := range selectorFamilies() {
		t.Run(family.name, func(t *testing.T) {
			list := []interface{}{family.sample}
			cfg := map[string]interface{}{family.field: list}
			valid := family.new(cfg)
			list[0] = "private-selector-sentinel"
			invalid := family.new(cfg)
			list[0] = family.sample
			delete(cfg, family.field)
			if err := configError(t, valid); err != nil {
				t.Fatal("caller mutation invalidated original configuration")
			}
			if err := configError(t, invalid); err == nil {
				t.Fatal("caller mutation repaired original invalid configuration")
			}
			// Opt-in validation must not change the legacy skipped-selector behavior.
			selectedCheck(t, invalid, family.examples[family.sample], true)
			selectedCheck(t, valid, family.examples[family.sample], false)
		})
	}
}

func TestSelectedConfigLanguage(t *testing.T) {
	for i, cfg := range []map[string]interface{}{nil, {}, {"allowed_scripts": []interface{}{}}} {
		t.Run(fmt.Sprintf("unrestricted_%d", i), func(t *testing.T) {
			g := language.New(cfg)
			if err := configError(t, g); err != nil {
				t.Fatal(err)
			}
			selectedCheck(t, g, "Привет как дела сегодня друзья", true)
		})
	}
	for _, script := range []string{"latin", "cyrillic", "arabic", "han", "japanese", "korean", "devanagari", "greek", "hebrew", "thai", "other", "LATIN"} {
		t.Run(script, func(t *testing.T) {
			if err := configError(t, language.New(map[string]interface{}{"allowed_scripts": []interface{}{script}})); err != nil {
				t.Fatal(err)
			}
		})
	}
	for name, value := range map[string]interface{}{
		"scalar": "latin", "null": nil, "object": map[string]interface{}{},
		"wrong_element": []interface{}{42}, "mixed_element": []interface{}{"latin", 42},
		"typed_slice_ignored_by_constructor": []string{"latin"},
	} {
		t.Run(name, func(t *testing.T) {
			g := language.New(map[string]interface{}{"allowed_scripts": value})
			if err := configError(t, g); err == nil {
				t.Fatal("malformed scripts accepted")
			}
		})
	}
	cfg := map[string]interface{}{"allowed_scripts": "latin"}
	invalid := language.New(cfg)
	cfg["allowed_scripts"] = []interface{}{"LATIN"}
	valid := language.New(cfg)
	cfg["allowed_scripts"] = 42
	if err := configError(t, invalid); err == nil {
		t.Fatal("original malformed scripts lost")
	}
	if err := configError(t, valid); err != nil {
		t.Fatal(err)
	}
	selectedCheck(t, invalid, "Привет как дела сегодня друзья", true)
	selectedCheck(t, valid, "Привет как дела сегодня друзья", false)
	selectedCheck(t, valid, "hello world", true)
}

func TestSelectedConfigTopics(t *testing.T) {
	for _, key := range []string{"allowed_topics", "forbidden_topics"} {
		t.Run(key, func(t *testing.T) {
			for name, value := range map[string]interface{}{
				"null": nil, "scalar": "restricted", "object": map[string]interface{}{},
				"wrong_entry": []interface{}{42}, "empty_entry": []interface{}{map[string]interface{}{}},
				"scalar_keywords": []interface{}{map[string]interface{}{"name": "restricted", "keywords": "restricted"}},
				"null_keywords":   []interface{}{map[string]interface{}{"keywords": nil}},
				"empty_keywords":  []interface{}{map[string]interface{}{"keywords": []interface{}{}}},
				"mixed_keywords":  []interface{}{map[string]interface{}{"keywords": []interface{}{"restricted", 42}}},
				"wrong_name":      []interface{}{map[string]interface{}{"name": 42, "keywords": []interface{}{"restricted"}}},
				"mixed_entries":   []interface{}{map[string]interface{}{"keywords": []interface{}{"restricted"}}, 42},
			} {
				t.Run(name, func(t *testing.T) {
					if err := configError(t, topic.New(map[string]interface{}{key: value})); err == nil {
						t.Fatal("malformed topics accepted")
					}
				})
			}
			for i, cfg := range []map[string]interface{}{nil, {}, {key: []interface{}{}}} {
				t.Run(fmt.Sprintf("unrestricted_%d", i), func(t *testing.T) {
					g := topic.New(cfg)
					if err := configError(t, g); err != nil {
						t.Fatal(err)
					}
					selectedCheck(t, g, "anything goes", true)
				})
			}
			// Topic keywords are literal strings, not regex; a bracket is valid.
			entry := map[string]interface{}{"keywords": []interface{}{"[", "restricted"}}
			cfg := map[string]interface{}{key: []interface{}{entry}}
			valid := topic.New(cfg)
			entry["keywords"] = "private-topic-sentinel"
			invalid := topic.New(cfg)
			entry["keywords"] = []interface{}{"restricted"}
			if err := configError(t, valid); err != nil {
				t.Fatal(err)
			}
			err := configError(t, invalid)
			if err == nil {
				t.Fatal("original malformed nested keywords lost")
			}
			if strings.Contains(err.Error(), "private-topic-sentinel") {
				t.Fatal("raw keywords leaked")
			}
			selectedCheck(t, valid, "RESTRICTED", key == "allowed_topics")
			selectedCheck(t, valid, "[", key == "allowed_topics")
			selectedCheck(t, valid, "unrelated", key == "forbidden_topics")
			selectedCheck(t, invalid, "restricted", true) // Legacy skipped entry unchanged.
		})
	}
}

func TestSelectedConfigGoRegexParity(t *testing.T) {
	patterns := []struct {
		name, pattern, match string
		valid                bool
	}{
		{"plain", "hello", "hello", true},
		{"empty_matches_all", "", "hello", true},
		{"unicode_class", `\p{Greek}+`, "αβγ", true},
		{"unicode_short_class", `\pL+`, "hello", true},
		{"unicode_negated_class", `\P{Greek}+`, "hello", true},
		{"quoted_literal", `\Q[a]+\E`, "[a]+", true},
		{"hex_codepoint", `\x{1F600}`, string(rune(0x1F600)), true},
		{"go_end_text", `hello\z`, "hello", true},
		{"ungreedy", `(?U)a+`, "aaa", true},
		{"named_capture", `(?P<word>hello)`, "hello", true},
		{"angle_named_capture", `(?<word>hello)`, "hello", true},
		{"posix_class", `[[:alpha:]]+`, "hello", true},
		{"scoped_flags", `(?i:HELLO)`, "hello", true},
		{"repeat_limit", `a{1000}`, strings.Repeat("a", 1000), true},
		{"malformed", "[", "", false},
		{"lookahead_python_only", `hello(?=world)`, "", false},
		{"lookbehind_python_only", `(?<=hello)world`, "", false},
		{"backreference_python_only", `(a)\1`, "", false},
		{"named_backreference_python_only", `(?P<x>a)(?P=x)`, "", false},
		{"python_end_text", `hello\Z`, "", false},
		{"python_unicode_flag", `(?u)hello`, "", false},
		{"python_verbose_flag", `(?x)hello`, "", false},
		{"atomic_group_python_only", `(?>hello)`, "", false},
		{"possessive_python_only", `a++`, "", false},
		{"repeat_over_limit", `a{1001}`, "", false},
	}
	for _, key := range []string{"deny_patterns", "require_patterns"} {
		for _, tc := range patterns {
			t.Run(key+"/"+tc.name, func(t *testing.T) {
				_, compileErr := regexp.Compile(tc.pattern)
				if (compileErr == nil) != tc.valid {
					t.Fatal("fixture disagrees with Go regexp")
				}
				g := validation.New(map[string]interface{}{key: []interface{}{tc.pattern}})
				err := configError(t, g)
				if (err == nil) != tc.valid {
					t.Fatalf("configuration error = %v, want valid %v", err, tc.valid)
				}
				if (g.RealtimeReady() == nil) != tc.valid {
					t.Fatal("regex readiness behavior changed")
				}
				if !tc.valid {
					return
				}
				selectedCheck(t, g, tc.match, key == "require_patterns")
			})
		}
	}
}

func TestSelectedConfigMalformedPatternsAndSnapshot(t *testing.T) {
	for _, key := range []string{"deny_patterns", "require_patterns"} {
		t.Run(key, func(t *testing.T) {
			for name, value := range map[string]interface{}{
				"null": nil, "scalar": "hello", "wrong_element": []interface{}{42},
				"mixed_element": []interface{}{"hello", 42}, "mixed_regex": []interface{}{"hello", "["},
				"typed_slice_ignored_by_constructor": []string{"hello"},
			} {
				t.Run(name, func(t *testing.T) {
					g := validation.New(map[string]interface{}{key: value})
					if err := configError(t, g); err == nil {
						t.Fatal("malformed pattern list accepted")
					}
					if g.RealtimeReady() == nil {
						t.Fatal("existing regex readiness lost")
					}
				})
			}
			list := []interface{}{"hello"}
			cfg := map[string]interface{}{key: list}
			valid := validation.New(cfg)
			list[0] = "[private-pattern-sentinel"
			invalid := validation.New(cfg)
			list[0] = "hello"
			if err := configError(t, valid); err != nil {
				t.Fatal(err)
			}
			err := configError(t, invalid)
			if err == nil {
				t.Fatal("original invalid regex lost")
			}
			if err != invalid.RealtimeReady() {
				t.Fatal("regex validation must reuse readiness error")
			}
			if strings.Contains(err.Error(), "private-pattern-sentinel") {
				t.Fatal("raw regex leaked")
			}
			selectedCheck(t, invalid, "anything", true) // Legacy invalid regex still skipped.
		})
	}
}

func TestSelectedConfigLengths(t *testing.T) {
	for _, key := range []string{"min_message_length", "max_message_length", "max_total_length"} {
		t.Run(key, func(t *testing.T) {
			for name, value := range map[string]interface{}{
				"null": nil, "string": "private-length-sentinel", "boolean": true, "array": []interface{}{1},
				"negative_int": -1, "negative_float": -1.0, "fraction": 1.5,
				"nan": math.NaN(), "infinity": math.Inf(1), "negative_infinity": math.Inf(-1),
				"overflow": math.Ldexp(1, strconv.IntSize-1),
			} {
				t.Run(name, func(t *testing.T) {
					cfg := map[string]interface{}{key: value}
					g := validation.New(cfg)
					cfg[key] = 0
					err := configError(t, g)
					if err == nil {
						t.Fatal("invalid length accepted")
					}
					if strings.Contains(err.Error(), "private-length-sentinel") {
						t.Fatal("raw length leaked")
					}
					// Length validation is selected-only, not a global realtime expansion.
					if g.RealtimeReady() != nil {
						t.Fatal("length validation broadened RealtimeReady")
					}
				})
			}
			for i, value := range []interface{}{0, 0.0, 1, 1.0, 100, int(^uint(0) >> 1)} {
				t.Run(fmt.Sprintf("valid_%d", i), func(t *testing.T) {
					cfg := map[string]interface{}{key: value}
					g := validation.New(cfg)
					cfg[key] = "invalid"
					if err := configError(t, g); err != nil {
						t.Fatal(err)
					}
				})
			}
		})
	}
	for _, key := range []string{"max_message_length", "max_total_length"} {
		t.Run("contradiction_"+key, func(t *testing.T) {
			g := validation.New(map[string]interface{}{"min_message_length": 5, key: 4})
			if err := configError(t, g); err == nil {
				t.Fatal("contradictory limits accepted")
			}
			if g.RealtimeReady() != nil {
				t.Fatal("length validation broadened RealtimeReady")
			}
		})
	}
	for i, cfg := range []map[string]interface{}{
		nil, {}, {"deny_patterns": []interface{}{}, "require_patterns": []interface{}{}},
		{"min_message_length": 0, "max_message_length": 0, "max_total_length": 0},
		{"min_message_length": 5, "max_message_length": 0, "max_total_length": 0},
		{"min_message_length": 5, "max_message_length": 5, "max_total_length": 5},
	} {
		t.Run(fmt.Sprintf("defaults_and_disabled_limits_%d", i), func(t *testing.T) {
			g := validation.New(cfg)
			if err := configError(t, g); err != nil {
				t.Fatal(err)
			}
			selectedCheck(t, g, "hello", true)
		})
	}
	selectedCheck(t, validation.New(map[string]interface{}{"max_message_length": -1}), "legacy negative limit disabled", true)
	selectedCheck(t, validation.New(map[string]interface{}{"max_message_length": 4}), "hello", false)
	selectedCheck(t, validation.New(map[string]interface{}{"min_message_length": 6}), "hello", false)
	selectedCheck(t, validation.New(map[string]interface{}{"max_total_length": 4}), "hello", false)
}
