package modeldb

import (
	"encoding/json"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// calendarPath is the shipped calendar, relative to this package.
const calendarPath = "../../catalog/deprecations.json"

func loadCalendar(t *testing.T) *Deprecations {
	t.Helper()
	data, err := os.ReadFile(calendarPath)
	if err != nil {
		t.Fatalf("read %s: %v", calendarPath, err)
	}
	cal, err := ParseDeprecations(data)
	if err != nil {
		t.Fatalf("parse %s: %v", calendarPath, err)
	}
	return cal
}

func day(s string) time.Time {
	t, err := time.Parse(time.RFC3339, s)
	if err != nil {
		panic(err)
	}
	return t
}

// A model past its shutdown date is refused (the request path answers 410
// model_expired with this reason); one before it, one a host still
// serves, and one the calendar does not list are not.
func TestShutdownModelRejected(t *testing.T) {
	cal := loadCalendar(t)
	today := day("2026-10-16T12:00:00Z")

	for _, tc := range []struct{ host, model, date, replacement string }{
		{"openai", "dall-e-3", "2026-05-12", "gpt-image-2"},
		{"", "openai/dall-e-3", "2026-05-12", "gpt-image-2"},
		{"gemini", "gemini-2.5-flash-image", "2026-10-02", "gemini-3.1-flash-image"},
		{"vertex", "gemini/gemini-2.5-flash-image", "2026-10-02", "gemini-3.1-flash-image"},
		{"gemini", "imagen-4.0-generate-001", "2026-08-17", "gemini-3.1-flash-image"},
	} {
		d, gone := cal.ShutDown(tc.host, tc.model, today)
		if !gone {
			t.Errorf("%s via %q: not refused on %s, want refused (shut down %s)", tc.model, tc.host, today.Format(time.DateOnly), tc.date)
			continue
		}
		if d.ShutdownDate != tc.date || d.Replacement != tc.replacement {
			t.Errorf("%s: entry = %s → %s, want %s → %s", tc.model, d.ShutdownDate, d.Replacement, tc.date, tc.replacement)
		}
		if r := d.Reason(); !strings.Contains(r, tc.date) || !strings.Contains(r, tc.replacement) {
			t.Errorf("%s: reason %q names neither the date nor the replacement", tc.model, r)
		}
	}

	// gpt-image-1 serves through 2026-10-22 and is refused from the start of
	// 2026-10-23 (UTC).
	if _, gone := cal.ShutDown("openai", "gpt-image-1", day("2026-10-22T23:59:59Z")); gone {
		t.Error("gpt-image-1 refused on 2026-10-22, the day before its shutdown")
	}
	if _, gone := cal.ShutDown("openai", "gpt-image-1", day("2026-10-23T00:00:00Z")); !gone {
		t.Error("gpt-image-1 not refused on its shutdown date 2026-10-23")
	}

	// Azure retires gpt-image-1-mini later than OpenAI does.
	dec := day("2026-12-15T00:00:00Z")
	if _, gone := cal.ShutDown("openai", "gpt-image-1-mini", dec); !gone {
		t.Error("OpenAI gpt-image-1-mini not refused after 2026-12-01")
	}
	if _, gone := cal.ShutDown("azure", "gpt-image-1-mini", dec); gone {
		t.Error("Azure gpt-image-1-mini refused before Azure's 2027-04-07 retirement")
	}
	if _, gone := cal.ShutDown("azure", "gpt-image-1-mini", day("2027-04-07T00:00:00Z")); !gone {
		t.Error("Azure gpt-image-1-mini not refused on 2027-04-07")
	}

	// Vertex still serves Imagen 4, which only the Gemini API shut down; the
	// replacements and unlisted models are never refused.
	for _, tc := range []struct{ host, model string }{
		{"vertex", "imagen-4.0-generate-001"},
		// A provider named "gemini" on Vertex AI: the model's provider-ID
		// prefix is not the host.
		{"vertex", "gemini/imagen-4.0-generate-001"},
		{"openai", "gpt-image-2"},
		{"gemini", "gemini-3.1-flash-image"},
		{"openai", "gpt-4o"},
	} {
		if d, gone := cal.ShutDown(tc.host, tc.model, today); gone {
			t.Errorf("%s via %s refused: %s", tc.model, tc.host, d.Reason())
		}
	}
	var none *Deprecations
	if _, gone := none.ShutDown("openai", "dall-e-3", today); gone {
		t.Error("an empty calendar refused a model")
	}
}

// The host a lookup takes is the provider's type, never its gateway ID: an
// Azure provider an org named "azure-eastus" still gets Azure's later
// retirement of gpt-image-1-mini, and a Gemini-format provider on Vertex AI
// still serves Imagen 4, which only the Gemini API shut down.
func TestDeprecationHostIsTheProviderType(t *testing.T) {
	cal := loadCalendar(t)
	dec := day("2026-12-15T00:00:00Z")

	for _, tc := range []struct{ format, baseURL, want string }{
		{"azure", "https://acme-eastus.openai.azure.com", "azure"},
		{" Azure ", "https://acme.openai.azure.com", "azure"},
		{"openai", "https://api.openai.com", "openai"},
		{"gemini", "https://generativelanguage.googleapis.com", "gemini"},
		{"gemini", "https://us-central1-aiplatform.googleapis.com/v1/projects/p/locations/us-central1", "vertex"},
		{"openai", "https://us-central1-aiplatform.googleapis.com/v1/projects/p/locations/us-central1/endpoints/openapi", "openai"},
		// "google" is the registry's alias of the Gemini adapter.
		{"google", "https://generativelanguage.googleapis.com", "gemini"},
		{"google", "https://us-central1-aiplatform.googleapis.com/v1/projects/p/locations/us-central1", "vertex"},
	} {
		if got := DeprecationHost(tc.format, tc.baseURL); got != tc.want {
			t.Errorf("DeprecationHost(%q, %q) = %q, want %q", tc.format, tc.baseURL, got, tc.want)
		}
	}

	// The org's Azure provider "azure-eastus": its ID misses the Azure
	// entry and falls to OpenAI's earlier date, its type finds Azure's.
	if _, gone := cal.ShutDown("azure-eastus", "gpt-image-1-mini", dec); !gone {
		t.Error("the provider ID unexpectedly matched the Azure entry; this test does not show why the type is needed")
	}
	host := DeprecationHost("azure", "https://acme-eastus.openai.azure.com")
	if d, gone := cal.ShutDown(host, "gpt-image-1-mini", dec); gone {
		t.Errorf("Azure gpt-image-1-mini refused on 2026-12-15 through host %q: %s", host, d.Reason())
	}

	today := day("2026-10-16T12:00:00Z")
	vertex := DeprecationHost("gemini", "https://us-central1-aiplatform.googleapis.com/v1/projects/p/locations/us-central1")
	if d, gone := cal.ShutDown(vertex, "imagen-4.0-generate-001", today); gone {
		t.Errorf("Imagen 4 refused on Vertex AI: %s", d.Reason())
	}
	if _, gone := cal.ShutDown(DeprecationHost("gemini", "https://generativelanguage.googleapis.com"), "imagen-4.0-generate-001", today); !gone {
		t.Error("Imagen 4 not refused on the Gemini API after 2026-08-17")
	}
}

// No gateway default names an OpenAI image model that is shut down or about
// to be: the calendar carries gpt-image-1 (2026-10-23), gpt-image-1.5 and
// gpt-image-1-mini (2026-12-01) and dall-e-3 (2026-05-12), and no non-test
// Go file under internal/ quotes any model id the calendar lists. (No
// gateway file holds an OpenAI image default; this keeps one from
// arriving with a model that is going away.) Code that must name a retired
// id on purpose, such as a map from a retired id to its replacement, marks
// that line with calendarAllowMarker and a reason.
func TestOpenAIImageDefaultNotDeprecated(t *testing.T) {
	cal := loadCalendar(t)
	for model, date := range map[string]string{
		"gpt-image-1":      "2026-10-23",
		"gpt-image-1.5":    "2026-12-01",
		"gpt-image-1-mini": "2026-12-01",
		"dall-e-3":         "2026-05-12",
	} {
		d, ok := cal.Lookup("openai", model)
		if !ok || d.ShutdownDate != date {
			t.Errorf("calendar: %s = %q (listed %v), want %s", model, d.ShutdownDate, ok, date)
		}
	}

	var quoted []string
	for key := range cal.byKey {
		quoted = append(quoted, `"`+key[strings.LastIndexByte(key, '/')+1:]+`"`)
	}
	err := filepath.WalkDir("..", func(path string, e fs.DirEntry, err error) error {
		if err != nil || e.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return err
		}
		src, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		for i, line := range strings.Split(string(src), "\n") {
			for _, q := range quoted {
				if strings.Contains(line, q) && !calendarAllowed(line) {
					t.Errorf("%s:%d names %s, which the shutdown calendar lists (mark a deliberate use with %q and a reason)", path, i+1, q, calendarAllowMarker)
				}
			}
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}

	for line, want := range map[string]bool{
		`	"dall-e-3": "gpt-image-2", // calendar:allow retired id mapped to its replacement`: true,
		`	"dall-e-3": "gpt-image-2", // calendar:allow`:                                      false,
		`	defaultImageModel = "dall-e-3"`:                                                    false,
	} {
		if got := calendarAllowed(line); got != want {
			t.Errorf("calendarAllowed(%q) = %v, want %v", line, got, want)
		}
	}
}

// calendarAllowMarker, followed by a reason on the same line, exempts that
// line of non-test code from TestOpenAIImageDefaultNotDeprecated.
const calendarAllowMarker = "// calendar:allow"

// calendarAllowed reports whether line carries calendarAllowMarker with a
// reason after it.
func calendarAllowed(line string) bool {
	_, reason, ok := strings.Cut(line, calendarAllowMarker)
	return ok && strings.TrimSpace(reason) != ""
}

// Every date the vendored litellm data also carries for a calendar entry
// agrees with it, and no replacement is itself shut down by the day the
// model it replaces goes.
func TestDeprecationsAgreeWithLiteLLM(t *testing.T) {
	cal := loadCalendar(t)
	var rows map[string]struct {
		DeprecationDate string `json:"deprecation_date"`
	}
	if err := json.Unmarshal(liteLLMData, &rows); err != nil {
		t.Fatal(err)
	}
	checked := 0
	for key, d := range cal.byKey {
		if row, ok := rows[key]; ok && row.DeprecationDate != "" {
			checked++
			if row.DeprecationDate != d.ShutdownDate {
				t.Errorf("%s: calendar %s, litellm %s", key, d.ShutdownDate, row.DeprecationDate)
			}
		}
		host, _, _ := strings.Cut(key, "/")
		if !strings.Contains(key, "/") {
			host = ""
		}
		if r, gone := cal.ShutDown(host, d.Replacement, d.shutdown); gone {
			t.Errorf("%s is replaced by %s, which is itself shut down: %s", key, d.Replacement, r.Reason())
		}
	}
	if checked < cal.Len()/2 {
		t.Errorf("only %d of %d calendar entries were checked against litellm", checked, cal.Len())
	}
}

func TestParseDeprecationsRefusesBadEntries(t *testing.T) {
	for name, body := range map[string]string{
		"not json":       `{`,
		"bad date":       `{"models":{"m":{"shutdown_date":"23/10/2026","replacement":"n"}}}`,
		"no date":        `{"models":{"m":{"replacement":"n"}}}`,
		"no replacement": `{"models":{"m":{"shutdown_date":"2026-10-23"}}}`,
		"no model id":    `{"models":{"azure/":{"shutdown_date":"2026-10-23","replacement":"n"}}}`,
	} {
		if _, err := ParseDeprecations([]byte(body)); err == nil {
			t.Errorf("%s: parsed, want an error", name)
		}
	}
}
