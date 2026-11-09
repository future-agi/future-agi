package cost

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ocrFixture is a recorded-shape Mistral OCR response of pages pages; with
// docAnnotation it carries a document annotation, and the first
// annotatedImages pages carry an image with a bbox annotation.
func ocrFixture(t *testing.T, pages int, docAnnotation bool, annotatedImages int) *models.OCRResponse {
	t.Helper()
	var b strings.Builder
	b.WriteString(`{"model":"mistral-ocr-4-0","pages":[`)
	for i := 0; i < pages; i++ {
		if i > 0 {
			b.WriteString(",")
		}
		img := ""
		if i < annotatedImages {
			img = `{"id":"img-` + fmt.Sprint(i) + `.jpeg","image_annotation":"{\"kind\":\"chart\"}"}`
		}
		fmt.Fprintf(&b, `{"index":%d,"markdown":"page %d","images":[%s],"header":"ACME","footer":"p%d"}`, i, i, img, i)
	}
	b.WriteString(`]`)
	if docAnnotation {
		b.WriteString(`,"document_annotation":"{\"invoice\":\"42\"}"`)
	} else {
		b.WriteString(`,"document_annotation":null`)
	}
	fmt.Fprintf(&b, `,"usage_info":{"pages_processed":%d,"doc_size_bytes":91234}}`, pages)
	var resp models.OCRResponse
	if err := json.Unmarshal([]byte(b.String()), &resp); err != nil {
		t.Fatalf("fixture: %v", err)
	}
	return &resp
}

// ocrRC is an OCR request served by provider for model, managed or BYOK.
func ocrRC(provider, model string, managed bool, resp *models.OCRResponse) *models.RequestContext {
	rc := modalityRC("ocr", model)
	rc.Provider = provider
	rc.OCRRequest = &models.OCRRequest{Model: model, Document: models.OCRDocument{Type: "document_url", DocumentURL: "https://example.com/a.pdf"}}
	rc.OCRResponse = resp
	if managed {
		rc.Metadata["billing_mode"] = models.BillingModeManaged
	}
	return rc
}

// OCR is priced per processed page (usage_info.pages_processed ×
// ocr_cost_per_page): a managed 10-page Mistral OCR 4 call is 10 × $0.004 =
// 40,000 upstream micros, billed with the 5% markup, and records its pages.
func TestOCRPricedPerPage(t *testing.T) {
	p := bundledPlugin()
	for _, model := range []string{"mistral-ocr-4-0", "mistral/mistral-ocr-4-0"} {
		rc := ocrRC("mistral", model, true, ocrFixture(t, 10, false, 0))
		p.FinalizeRequest(rc)

		if rc.Billing.UpstreamMicros != 40_000 {
			t.Errorf("%s: upstream micros = %d, want 40,000", model, rc.Billing.UpstreamMicros)
		}
		if rc.Billing.BilledMicros != 42_000 {
			t.Errorf("%s: billed micros = %d, want 42,000 (5%% markup)", model, rc.Billing.BilledMicros)
		}
		if got := rc.Metadata["cost"]; got != "0.042000" {
			t.Errorf("%s: cost = %q, want 0.042000", model, got)
		}
		if got := rc.Billing.CostSource; got != models.CostSourceModelDB {
			t.Errorf("%s: cost source = %q, want modeldb", model, got)
		}
		if got := rc.Metadata[models.OCRPagesMetadataKey]; got != "10" {
			t.Errorf("%s: %s = %q, want 10", model, models.OCRPagesMetadataKey, got)
		}
	}
}

// Annotated pages add annotation_cost_per_page ($0.005 for Mistral OCR 4):
// a document annotation covers every processed page, and without one a page
// counts when one of its images carries a bbox annotation.
func TestOCRAnnotationPriced(t *testing.T) {
	p := bundledPlugin()
	cases := []struct {
		name                string
		doc                 bool
		annotatedImagePages int
		want                int64
	}{
		{"document annotation", true, 0, 10*4_000 + 10*5_000},
		{"bbox annotations on 2 pages", false, 2, 10*4_000 + 2*5_000},
		{"no annotation", false, 0, 10 * 4_000},
	}
	for _, tc := range cases {
		rc := ocrRC("mistral", "mistral-ocr-4-0", true, ocrFixture(t, 10, tc.doc, tc.annotatedImagePages))
		p.FinalizeRequest(rc)
		if rc.Billing.UpstreamMicros != tc.want {
			t.Errorf("%s: upstream micros = %d, want %d", tc.name, rc.Billing.UpstreamMicros, tc.want)
		}
	}
}

// A BYOK OCR call is free (nothing billed to the org) but metered: its pages
// are recorded and its upstream cost is estimated at the list price, as for
// every other BYOK request.
func TestOCRByokMeteredFree(t *testing.T) {
	p := bundledPlugin()
	rc := ocrRC("mistral", "mistral-ocr-4-0", false, ocrFixture(t, 3, false, 0))
	p.FinalizeRequest(rc)

	if rc.Billing.BilledMicros != 0 {
		t.Errorf("billed micros = %d, want 0: the org pays its own upstream", rc.Billing.BilledMicros)
	}
	if rc.Billing.UpstreamMicros != 12_000 {
		t.Errorf("upstream estimate = %d micros, want 12,000 (3 × $0.004)", rc.Billing.UpstreamMicros)
	}
	if got := rc.Metadata["cost"]; got != "0.012000" {
		t.Errorf("cost = %q, want the BYOK estimate 0.012000", got)
	}
	if got := rc.Metadata[models.OCRPagesMetadataKey]; got != "3" {
		t.Errorf("%s = %q, want 3", models.OCRPagesMetadataKey, got)
	}

	// A recorded BYOK attempt carries the estimate and stays BYOK.
	rc = ocrRC("mistral", "mistral-ocr-4-0", false, ocrFixture(t, 3, false, 0))
	rc.RequestID = "req-ocr"
	rc.AddAttempt(models.Attempt{Kind: models.AttemptPrimary, Provider: "mistral", Model: "mistral-ocr-4-0",
		CredentialType: models.CredentialBYOK, Outcome: models.OutcomeSuccess, StatusCode: 200})
	p.FinalizeRequest(rc)
	attempts := rc.AttemptsSnapshot()
	if len(attempts) != 1 {
		t.Fatalf("attempts = %d, want 1", len(attempts))
	}
	a := attempts[0]
	if !a.Billable || a.UpstreamCostMicros != 12_000 || a.BilledCostMicros != 12_000 || a.LandedCostMicros != 0 {
		t.Errorf("attempt = billable %v upstream %d billed %d landed %d, want true 12000 12000 0",
			a.Billable, a.UpstreamCostMicros, a.BilledCostMicros, a.LandedCostMicros)
	}
	if rc.Billing.BilledMicros != 0 {
		t.Errorf("billed micros = %d, want 0", rc.Billing.BilledMicros)
	}
}

// An OCR model with no per-page price is unpriced, never billed at zero as
// if priced, and its pages are still metered.
func TestOCRUnpricedModelStillMetered(t *testing.T) {
	p := bundledPlugin()
	rc := ocrRC("mistral", "ocr-nobody-prices", false, ocrFixture(t, 4, false, 0))
	p.FinalizeRequest(rc)

	if got := rc.Metadata["cost_source"]; got != string(models.CostSourceUnpriced) {
		t.Errorf("cost_source = %q, want unpriced", got)
	}
	if _, ok := rc.Metadata["cost"]; ok {
		t.Errorf("cost = %q, want none for an unpriced request", rc.Metadata["cost"])
	}
	if got := rc.Metadata[models.OCRPagesMetadataKey]; got != "4" {
		t.Errorf("%s = %q, want 4", models.OCRPagesMetadataKey, got)
	}
}

// A request whose OCR call failed was never served: nothing is priced or
// metered.
func TestOCRNotServedNotPriced(t *testing.T) {
	p := bundledPlugin()
	rc := ocrRC("mistral", "mistral-ocr-4-0", true, nil)
	p.FinalizeRequest(rc)
	if rc.Billing.UpstreamMicros != 0 || rc.Metadata[models.OCRPagesMetadataKey] != "" {
		t.Errorf("unserved OCR request priced: upstream %d, pages %q", rc.Billing.UpstreamMicros, rc.Metadata[models.OCRPagesMetadataKey])
	}
}
