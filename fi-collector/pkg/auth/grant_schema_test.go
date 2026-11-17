package auth

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"testing"
)

const grantSchemaPin = "909dd942d96f1c4afd4ad389ed8df3d9b3827bc9"
const grantSchemaDigest = "sha256:ca5779a3e6dbe8ebc06264607ef24ebe88a8712ccd5e0f1234666fa566671f56"

func TestGrantColumnsMatchPinnedIssuerMigration(t *testing.T) {
	raw, err := os.ReadFile("testdata/service_grant_migration.txt")
	if err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256(raw)
	if "sha256:"+hex.EncodeToString(digest[:]) != grantSchemaDigest {
		t.Fatalf("migration differs from issuer pin %s", grantSchemaPin)
	}
	model := strings.Split(string(raw), "name=\"BatchCancellationIntent\"")[0]
	fields := regexp.MustCompile(`\(\s*"([a-z_]+)",\s*models\.[A-Za-z]+Field\(`).FindAllStringSubmatch(model, -1)
	want := []string{}
	for _, field := range fields {
		want = append(want, field[1])
	}
	sort.Strings(want)
	if len(want) != 17 {
		t.Fatalf("issuer columns=%d, want 17", len(want))
	}
	pool := routingDatabase(t)
	routingFixture{t, pool}.exec(grantSchema)
	rows, err := pool.Query(context.Background(), `SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='accounts_service_grant' ORDER BY column_name`)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	got := []string{}
	for rows.Next() {
		var column string
		if err := rows.Scan(&column); err != nil {
			t.Fatal(err)
		}
		got = append(got, column)
	}
	if err := rows.Err(); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("collector schema columns %v differ from issuer %v", got, want)
	}
	// On a composed tree, also compare the actual issuer migration, not only
	// the immutable snapshot that permits standalone collector development.
	composed, err := os.ReadFile("../../../futureagi/accounts/migrations/0032_service_grants.py")
	if err == nil {
		d := sha256.Sum256(composed)
		if d != digest {
			t.Fatal("composed issuer migration moved; review and repin its column contract")
		}
	} else if !os.IsNotExist(err) {
		t.Fatal(err)
	}
}
