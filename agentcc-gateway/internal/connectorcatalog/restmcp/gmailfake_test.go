package restmcp

import (
	"net/http"
	"strings"
	"testing"
)

// gmailFake answers like Gmail's REST API for the calls the tests make,
// with synthetic data (no real account).
func gmailFake(t *testing.T) *vendorFake {
	t.Helper()
	return newVendorFake(t, func(w http.ResponseWriter, r *http.Request) {
		p := r.URL.Path
		switch {
		case r.Method == http.MethodGet && p == "/gmail/v1/users/me/profile":
			jsonAnswer(200, map[string]any{"emailAddress": "someone@example.test", "messagesTotal": 3, "threadsTotal": 2,
				"historyId": "1001"})(w, r)
		case r.Method == http.MethodGet && p == "/gmail/v1/users/me/threads":
			jsonAnswer(200, map[string]any{
				"threads": []map[string]any{
					{"id": "t-1", "snippet": "Quarterly numbers", "historyId": "991"},
					{"id": "t-2", "snippet": "Lunch on Friday", "historyId": "992"},
				},
				"nextPageToken": "page-2", "resultSizeEstimate": 2,
			})(w, r)
		case r.Method == http.MethodGet && p == "/gmail/v1/users/me/threads/missing":
			jsonAnswer(404, map[string]any{"error": map[string]any{"code": 404, "message": "Requested entity was not found.",
				"status": "NOT_FOUND"}})(w, r)
		case r.Method == http.MethodGet && strings.HasPrefix(p, "/gmail/v1/users/me/threads/"):
			jsonAnswer(200, map[string]any{"id": strings.TrimPrefix(p, "/gmail/v1/users/me/threads/"), "historyId": "993",
				"messages": []map[string]any{{"id": "m-1", "threadId": "t-1", "labelIds": []string{"INBOX"}, "snippet": "Hi"}}})(w, r)
		case r.Method == http.MethodGet && p == "/gmail/v1/users/me/labels":
			jsonAnswer(200, map[string]any{"labels": []map[string]any{
				{"id": "INBOX", "name": "INBOX", "type": "system", "messageListVisibility": "hide"},
				{"id": "Label_1", "name": "Receipts", "type": "user"},
			}})(w, r)
		case r.Method == http.MethodPost && p == "/gmail/v1/users/me/messages/send":
			jsonAnswer(200, map[string]any{"id": "m-9", "threadId": "t-9", "labelIds": []string{"SENT"}})(w, r)
		default:
			jsonAnswer(404, map[string]any{"error": map[string]any{"code": 404, "message": "Not Found"}})(w, r)
		}
	})
}

// gmailRuntime is the runtime serving CN4's example Gmail app against the
// fake.
func gmailRuntime(t *testing.T, mutate ...func(*Options)) (*runtimeUnder, *vendorFake) {
	t.Helper()
	fake := gmailFake(t)
	app := compileOne(t, cn4Catalog(t), "gmail")
	return startRuntime(t, []*App{app}, map[string]string{gmailOrigin: fake.URL}, mutate...), fake
}
