package connectors

import (
	"bytes"
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

func TestRuntimeSnapshotIsPrivateAndDefensive(t *testing.T) {
	cat, err := Embedded()
	if err != nil {
		t.Fatal(err)
	}
	first, digest := cat.RuntimeSnapshot()
	if len(first) == 0 || len(first[0].Raw) == 0 || len(digest) != 64 {
		t.Fatal("missing embedded admission")
	}
	before := append([]byte(nil), first[0].Raw...)
	first[0].Raw[0] = 'x'
	first[0].Retired = !first[0].Retired
	first[0].AppID = "forged"
	second, again := cat.RuntimeSnapshot()
	if !bytes.Equal(second[0].Raw, before) || digest != again {
		t.Fatal("mutable snapshot escape")
	}
	fake := &Catalog{Records: cat.Records, Source: cat.Source}
	records, unbound := fake.RuntimeSnapshot()
	if unbound != "" || len(records) == 0 {
		t.Fatal("unadmitted catalog treatment")
	}
	for _, r := range records {
		if r.Raw != nil {
			t.Fatal("unadmitted record bytes")
		}
	}
}

func TestRuntimeCaptureSyntheticAdmittedRecord(t *testing.T) {
	// Unit fixture at the already-validated boundary only. This does not
	// bypass checkEnablement in Embedded, or approve any real declaration.
	raw := json.RawMessage(`{"app":"sample-app","id":"native-rest","version":"1.2.3","kind":"rest","x-gateway":{"connectionEnablement":{"schemaVersion":1,"enabled":true,"operations":["connect"]}}}`)
	provider := &manifest.Provider{App: "sample-app", ID: "native-rest", Kind: "rest"}
	app := &manifest.App{ID: "sample-app", Providers: []*manifest.Provider{provider}}
	typed := &manifest.Catalog{Apps: []*manifest.App{app}}
	data, _ := json.Marshal(map[string]any{"providers": []json.RawMessage{raw}})
	seal, err := captureRuntimeSnapshot(typed, data)
	if err != nil {
		t.Fatal(err)
	}
	c := &Catalog{Records: typed, runtimeSnapshot: seal}
	records, digest := c.RuntimeSnapshot()
	if len(records) != 1 || len(digest) != 64 || !bytes.Equal(records[0].Raw, raw) {
		t.Fatal("admitted bytes lost")
	}
	// Mutations of the original buffers and typed projection cannot change
	// the private evidence captured when this build admitted the bundle.
	data[0] = 'x'
	provider.Kind = "partner"
	app.ID = "other-app"
	next, nextDigest := c.RuntimeSnapshot()
	if !reflect.DeepEqual(records, next) || digest != nextDigest {
		t.Fatal("admission changed after validation")
	}
}

func TestRuntimeCaptureRefusesBadJoins(t *testing.T) {
	p := &manifest.Provider{App: "sample-app", ID: "native-rest", Kind: "rest"}
	cat := &manifest.Catalog{Apps: []*manifest.App{{ID: "sample-app", Providers: []*manifest.Provider{p}}}}
	raw := `{"app":"sample-app","id":"native-rest","kind":"rest"}`
	for _, data := range []string{`{}`, `{"providers":[]}`, `{"providers":[` + raw + `,` + raw + `]}`, `{"providers":[` + strings.Replace(raw, "native-rest", "other", 1) + `]}`, `{"providers":[` + strings.Replace(raw, "rest\"}", "partner\"}", 1) + `]}`} {
		if _, err := captureRuntimeSnapshot(cat, []byte(data)); err == nil {
			t.Fatalf("accepted %s", data)
		}
	}
}
