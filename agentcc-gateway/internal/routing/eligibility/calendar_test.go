package eligibility

import (
	"testing"
	"time"

	catalogdata "github.com/futureagi/agentcc-gateway/catalog"
)

// Rule 4 also reads the catalog's shutdown calendar
// (catalog/deprecations.json), so a model with only a calendar shutdown date
// in the past, and no catalog entry, is refused with model_expired (410),
// judged by the serving provider's type.
func TestCalendarShutdownIsModelExpired(t *testing.T) {
	cal, err := catalogdata.Deprecations()
	if err != nil {
		t.Fatal(err)
	}
	in := byokIn(nil)
	in.Model, in.Entry, in.Shutdowns, in.Host = "gemini-2.5-flash-image", nil, cal, "openai"
	v := Check(&in)
	if v.OK || v.Code != CodeModelExpired || StatusFor(v.Code) != 410 {
		t.Fatalf("gemini-2.5-flash-image (shut down 2026-10-02): verdict %+v, want model_expired (410)", v)
	}

	auto := in
	auto.Auto = true
	if v := Check(&auto); v.OK || v.Code != CodeModelExpired {
		t.Fatalf("auto candidate past its shutdown: %+v, want model_expired", v)
	}

	for name, tc := range map[string]struct {
		model, host string
		now         time.Time
	}{
		"replacement model":                {"gemini-3.1-flash-image", "gemini", testNow},
		"unlisted model":                   {"gpt-4o", "openai", testNow},
		"before the shutdown day":          {"gpt-image-1", "openai", time.Date(2026, 10, 22, 23, 0, 0, 0, time.UTC)},
		"Azure retires it later":           {"gpt-image-1-mini", "azure", time.Date(2026, 12, 15, 0, 0, 0, 0, time.UTC)},
		"Vertex still serves Imagen 4":     {"imagen-4.0-generate-001", "vertex", testNow},
		"provider-ID prefix is not a host": {"gemini/imagen-4.0-generate-001", "vertex", testNow},
	} {
		ok := byokIn(nil)
		ok.Model, ok.Entry, ok.Shutdowns, ok.Host, ok.Now = tc.model, nil, cal, tc.host, tc.now
		if v := Check(&ok); !v.OK {
			t.Errorf("%s: %s via %s refused: %s %s", name, tc.model, tc.host, v.Code, v.Reason)
		}
	}

	none := in
	none.Shutdowns = nil
	if v := Check(&none); !v.OK {
		t.Errorf("no calendar: refused %s", v.Code)
	}
}
