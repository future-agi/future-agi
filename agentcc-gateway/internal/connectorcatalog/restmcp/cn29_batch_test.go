package restmcp

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/catalog/connectors"
)

func TestCN29Batch(t *testing.T) {
	checkV10Batch(t, []string{"microsoft-sharepoint", "microsoft-excel"})
}

// Word/PowerPoint remain owed until bounded OOXML text extraction and version
// uploads are implemented. Do not ship duplicated OneDrive operations as apps.
func TestUnimplementedOfficeAppsAbsent(t *testing.T) {
	cat, err := connectors.LoadDir(repoPath(t, "agentcc-gateway/catalog/connectors"))
	if err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"microsoft-word", "microsoft-powerpoint"} {
		if cat.Records.App(id) != nil {
			t.Fatalf("%s must stay absent until document capabilities are implemented", id)
		}
	}
}
