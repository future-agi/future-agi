package credtemplate_test

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate"
)

// vectorFile is the placement rule as data: the gateway's Check passes
// every vector here, and ForCatalogSecret, with the file's catalog, every
// vector with a catalog_ref; Django's push-time check is held to the same
// file (), so the two sides cannot
// drift.
const vectorFile = "testdata/placement-vectors.json"

type vector struct {
	Name            string `json:"name"`
	URL             string `json:"url"`
	ReferenceHeader string `json:"reference_header"`
	CatalogRef      string `json:"catalog_ref"`
	NoCatalog       bool   `json:"no_catalog"` // placed with no catalog loaded
	Secret          struct {
		Type   string                    `json:"type"`
		Header string                    `json:"header"`
		In     string                    `json:"in"`
		Name   string                    `json:"name"`
		Value  string                    `json:"value"`
		Parts  []credtemplate.SecretPart `json:"parts"`
	} `json:"secret"`
	OK    bool   `json:"ok"`
	Field string `json:"field"`
}

// fixtureCatalog is the vector file's catalog: provider records in the
// shape of connector-catalog-bundle/v1's providers, as credtemplate.Catalog.
type fixtureCatalog struct {
	Description string `json:"description"`
	Providers   []struct {
		App     string `json:"app"`
		ID      string `json:"id"`
		Version string `json:"version"`
		Auth    struct {
			CredentialParts []struct {
				In     string `json:"in"`
				Name   string `json:"name"`
				Secret bool   `json:"secret"`
				Value  string `json:"value"`
				Prefix string `json:"prefix"`
			} `json:"credentialParts"`
		} `json:"auth"`
	} `json:"providers"`
}

func (c fixtureCatalog) CredentialParts(app, provider string) (string, []credtemplate.Declared, bool) {
	for _, p := range c.Providers {
		if p.App != app || p.ID != provider {
			continue
		}
		parts := make([]credtemplate.Declared, len(p.Auth.CredentialParts))
		for i, cp := range p.Auth.CredentialParts {
			parts[i] = credtemplate.Declared{In: credtemplate.In(cp.In), Name: cp.Name, Secret: cp.Secret, Value: cp.Value, Prefix: cp.Prefix}
		}
		return p.Version, parts, true
	}
	return "", nil, false
}

// TestPlacementVectors: Check answers each shared vector as it states, and
// ForCatalogSecret, with the file's catalog, each vector with a
// catalog_ref (Django holds the catalog rules to the same answers).
func TestPlacementVectors(t *testing.T) {
	raw, err := os.ReadFile(vectorFile)
	if err != nil {
		t.Fatal(err)
	}
	var doc struct {
		Description string         `json:"description"`
		Catalog     fixtureCatalog `json:"catalog"`
		Vectors     []vector       `json:"vectors"`
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&doc); err != nil {
		t.Fatalf("%s: %v", vectorFile, err)
	}
	catalogVectors := 0
	for _, v := range doc.Vectors {
		if v.CatalogRef != "" {
			catalogVectors++
		}
	}
	noCatalog := 0
	for _, v := range doc.Vectors {
		if v.NoCatalog {
			noCatalog++
			if v.CatalogRef == "" || v.OK {
				t.Errorf("%s: a no_catalog vector names a catalog_ref and is refused", v.Name)
			}
		}
	}
	if len(doc.Vectors)-catalogVectors < 40 || catalogVectors < 40 || len(doc.Catalog.Providers) < 10 || noCatalog < 3 {
		t.Fatalf("%s holds %d vectors, %d with a catalog_ref, and %d catalog providers",
			vectorFile, len(doc.Vectors), catalogVectors, len(doc.Catalog.Providers))
	}
	for _, v := range doc.Vectors {
		sec := credtemplate.Secret{Type: v.Secret.Type, Header: v.Secret.Header, ReferenceHeader: v.ReferenceHeader,
			In: v.Secret.In, Name: v.Secret.Name, Value: v.Secret.Value, Parts: v.Secret.Parts}
		err := credtemplate.Check(v.URL, sec)
		if v.CatalogRef != "" {
			var cat credtemplate.Catalog = doc.Catalog
			if v.NoCatalog {
				cat = nil
			}
			_, err = credtemplate.ForCatalogSecret(v.URL, sec, cat, v.CatalogRef)
		}
		var ce *credtemplate.Error
		switch {
		case v.OK && err != nil:
			t.Errorf("%s: refused: %v", v.Name, err)
		case !v.OK && (!errors.As(err, &ce) || ce.Field != v.Field):
			t.Errorf("%s: err %v, want a refusal on %q", v.Name, err, v.Field)
		}
	}
}
