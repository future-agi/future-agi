package emit

import (
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

func TestDocsCards(t *testing.T) {
	cat, meta := fixture(t)
	files := emitTarget(t, TargetDocs, cat, meta)
	index := string(files["docs/index.md"])
	for _, a := range cat.Apps {
		card, ok := files["docs/apps/"+a.ID+".md"]
		if !ok {
			t.Fatalf("no card for %s", a.ID)
		}
		if !strings.Contains(index, "](apps/"+a.ID+".md)") {
			t.Errorf("the index does not link %s", a.ID)
		}
		for _, p := range a.Providers {
			if !strings.Contains(string(card), "`"+catalogRef(p)+"`") {
				t.Errorf("%s's card does not name %s", a.ID, catalogRef(p))
			}
		}
	}
	if _, ok := files["docs/platforms/relay.md"]; !ok {
		t.Error("no card for platform relay")
	}
	if !strings.Contains(index, meta.Version) || !strings.Contains(index, "7 apps and 1 partner platform") {
		t.Errorf("index:\n%s", index)
	}
	gmail := string(files["docs/apps/gmail.md"])
	for _, want := range []string{"| `search_threads` |", "| `send_email` |", "Provider `futureagi-rest` (default)", "OAuth 2, with PKCE"} {
		if !strings.Contains(gmail, want) {
			t.Errorf("gmail's card lacks %q", want)
		}
	}

	// Text from the data renders as text, and a pending vendor approval is
	// stated, never hidden.
	odd := vendorApps(1)
	odd.Apps[0].Description = "Pipes | *stars* <script>alert(1)</script> [link](x)\nnext"
	odd.Apps[0].Providers[0].Auth.ClientApproval = &manifest.ClientApproval{
		Required: true, Program: "Vendor client review", URL: "https://vendor.example.com/apps", Source: "https://vendor.example.com/apps",
	}
	card := string(emitTarget(t, TargetDocs, odd, testMeta)["docs/apps/app00.md"])
	if !strings.Contains(card, `Pipes \| \*stars\* \<script\>alert(1)\</script\> \[link\](x) next`) {
		t.Errorf("the description is not escaped:\n%s", card)
	}
	if !strings.Contains(card, "Needs vendor approval: Vendor client review") {
		t.Errorf("a pending approval is not stated:\n%s", card)
	}
	if strings.Count(card, "<script>") != 0 {
		t.Error("raw HTML reached the card")
	}
}

// TestDocsCardsLabelGenericTools: a
// generic tool takes its operation as an argument, so its row says that a
// per-tool policy sees only the verb, and names the argument; no other row
// carries the label.
func TestDocsCardsLabelGenericTools(t *testing.T) {
	cat, meta := fixture(t)
	stripe := string(emitTarget(t, TargetDocs, cat, meta)["docs/apps/stripe.md"])
	rows := map[string]string{}
	for _, line := range strings.Split(stripe, "\n") {
		if strings.HasPrefix(line, "| `") {
			rows[strings.SplitN(line, "`", 3)[1]] = line
		}
	}
	for _, name := range []string{"stripe_api_read", "stripe_api_write"} {
		if !strings.Contains(rows[name], genericToolLabel) {
			t.Errorf("stripe's %s row lacks the generic label: %q", name, rows[name])
		}
	}
	labelled := 0
	for _, row := range rows {
		if strings.Contains(row, genericToolLabel) {
			labelled++
		}
	}
	if labelled != 2 {
		t.Errorf("%d rows carry the generic label, want stripe's two generic tools:\n%s", labelled, stripe)
	}
	// genericArguments are named.
	odd := vendorApps(1)
	odd.Apps[0].Providers[0].Tools.Items = []manifest.Tool{{Name: "call", Description: "Call any endpoint.", Generic: true,
		GenericArguments: []string{"path", "jsonBody"}}}
	card := string(emitTarget(t, TargetDocs, odd, testMeta)["docs/apps/app00.md"])
	if !strings.Contains(card, genericToolLabel+"; the operation is in `path`, `jsonBody`") {
		t.Errorf("the generic tool's arguments are not named:\n%s", card)
	}
}

// TestDocsCardsNameEachURLVariable: an
// endpoint with URL variables says what each variable admits, so a card never
// shows a bare {apiHost}: its description, pattern, default and who sets it.
func TestDocsCardsNameEachURLVariable(t *testing.T) {
	cat := vendorApps(1)
	p := cat.Apps[0].Providers[0]
	p.Transport.URL = "https://{apiHost}/mcp/{region}"
	p.Transport.URLVariables = map[string]manifest.URLVariable{
		"region":  {Description: "The account's region, such as eu", Pattern: "^(us|eu)$", Scope: "org"},
		"apiHost": {Description: "The API host | your instance", Pattern: "^[a-z0-9.-]+$", Default: "api.example.com", Scope: "user"},
	}
	card := string(emitTarget(t, TargetDocs, cat, testMeta)["docs/apps/app00.md"])
	for _, want := range []string{
		"| URL variable `apiHost` | The API host \\| your instance. Pattern `^[a-z0-9.-]+$`; default `api.example.com`; set by each user |",
		"| URL variable `region` | The account's region, such as eu. Pattern `^(us\\|eu)$`; no default; set by the org |",
	} {
		if !strings.Contains(card, want) {
			t.Errorf("the card lacks %q:\n%s", want, card)
		}
	}
	if strings.Index(card, "URL variable `apiHost`") > strings.Index(card, "URL variable `region`") {
		t.Error("the URL variables are not in name order")
	}
	if strings.Index(card, "| Endpoint |") > strings.Index(card, "URL variable `apiHost`") {
		t.Error("the URL variables do not follow the endpoint")
	}
}
