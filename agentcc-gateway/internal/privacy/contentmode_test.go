package privacy

import (
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The most restrictive of org, key, request header and global default wins,
// and Source names it; a tie names the least specific source.
func TestContentModePrecedence(t *testing.T) {
	for _, tt := range []struct {
		name string
		in   ContentModeSources
		want models.Privacy
	}{
		{"nothing set is full, from the default", ContentModeSources{},
			models.Privacy{ContentMode: models.ContentModeFull, Source: models.PrivacySourceDefault}},
		{"explicit full everywhere stays the default's", ContentModeSources{Org: "full", Key: "full", Request: "full", Default: "full"},
			models.Privacy{ContentMode: models.ContentModeFull, Source: models.PrivacySourceDefault}},
		{"org metadata", ContentModeSources{Org: "metadata"},
			models.Privacy{ContentMode: models.ContentModeMetadata, Source: models.PrivacySourceOrg}},
		{"key none beats org metadata", ContentModeSources{Org: "metadata", Key: "none"},
			models.Privacy{ContentMode: models.ContentModeNone, Source: models.PrivacySourceKey}},
		{"request none beats key metadata", ContentModeSources{Key: "metadata", Request: "none"},
			models.Privacy{ContentMode: models.ContentModeNone, Source: models.PrivacySourceRequest}},
		{"global default metadata beats org full", ContentModeSources{Org: "full", Default: "metadata"},
			models.Privacy{ContentMode: models.ContentModeMetadata, Source: models.PrivacySourceDefault}},
		{"a tie names the least specific source", ContentModeSources{Org: "none", Key: "none", Request: "none"},
			models.Privacy{ContentMode: models.ContentModeNone, Source: models.PrivacySourceOrg}},
		{"a tie with the default is the default's", ContentModeSources{Org: "metadata", Default: "metadata"},
			models.Privacy{ContentMode: models.ContentModeMetadata, Source: models.PrivacySourceDefault}},
		{"values are read case- and space-insensitively", ContentModeSources{Key: " Metadata "},
			models.Privacy{ContentMode: models.ContentModeMetadata, Source: models.PrivacySourceKey}},
		{"an unknown key value fails closed", ContentModeSources{Org: "metadata", Key: "nothing-please"},
			models.Privacy{ContentMode: models.ContentModeNone, Source: models.PrivacySourceKey}},
		{"an unknown default fails closed", ContentModeSources{Default: "fulll"},
			models.Privacy{ContentMode: models.ContentModeNone, Source: models.PrivacySourceDefault}},
	} {
		t.Run(tt.name, func(t *testing.T) {
			if got := ResolveContentMode(tt.in); got != tt.want {
				t.Errorf("ResolveContentMode(%+v) = %+v, want %+v", tt.in, got, tt.want)
			}
		})
	}
}

// The request header can tighten the org's mode but never loosen it, and a
// misspelt header tightens to none rather than being ignored.
func TestRequestHeaderOnlyTightens(t *testing.T) {
	header := func(v string) http.Header {
		h := http.Header{}
		h.Set("x-agentcc-log-content", v)
		return h
	}
	for _, tt := range []struct {
		name, org, header string
		want              models.ContentMode
		source            string
	}{
		{"full cannot loosen metadata", "metadata", "full", models.ContentModeMetadata, models.PrivacySourceOrg},
		{"full cannot loosen none", "none", "full", models.ContentModeNone, models.PrivacySourceOrg},
		{"metadata cannot loosen none", "none", "metadata", models.ContentModeNone, models.PrivacySourceOrg},
		{"none tightens full", "full", "none", models.ContentModeNone, models.PrivacySourceRequest},
		{"metadata tightens full", "full", "metadata", models.ContentModeMetadata, models.PrivacySourceRequest},
		{"a misspelt header tightens to none", "full", "metdata", models.ContentModeNone, models.PrivacySourceRequest},
	} {
		t.Run(tt.name, func(t *testing.T) {
			got := ResolveContentMode(ContentModeSources{Org: tt.org, Request: RequestContentMode(header(tt.header))})
			if got.ContentMode != tt.want || got.Source != tt.source {
				t.Errorf("org %s, header %s: got %+v, want %s from %s", tt.org, tt.header, got, tt.want, tt.source)
			}
		})
	}
	if got := RequestContentMode(nil); got != "" {
		t.Errorf("RequestContentMode(nil) = %q, want unset", got)
	}
	if got := RequestContentMode(http.Header{}); got != "" {
		t.Errorf("no header: RequestContentMode = %q, want unset", got)
	}
}

func TestDefaultContentModeSetAndRestore(t *testing.T) {
	if got := DefaultContentMode(); got != "full" {
		t.Fatalf("DefaultContentMode() = %q before any set, want full", got)
	}
	restore, err := SetDefaultContentMode("metadata")
	if err != nil {
		t.Fatal(err)
	}
	if got := DefaultContentMode(); got != "metadata" {
		t.Errorf("after set: %q, want metadata", got)
	}
	if got := ResolveContentMode(ContentModeSources{Default: DefaultContentMode()}); got.ContentMode != models.ContentModeMetadata {
		t.Errorf("the installed default does not resolve: %+v", got)
	}
	restore()
	if got := DefaultContentMode(); got != "full" {
		t.Errorf("after restore: %q, want full", got)
	}

	if _, err := SetDefaultContentMode("everything"); err == nil {
		t.Error("an unknown default was accepted")
	}
	if got := DefaultContentMode(); got != "full" {
		t.Errorf("a refused set changed the default to %q", got)
	}
	restore, err = SetDefaultContentMode("")
	if err != nil || DefaultContentMode() != "full" {
		t.Errorf(`SetDefaultContentMode("") = %v, default %q; want full`, err, DefaultContentMode())
	}
	restore()
}
