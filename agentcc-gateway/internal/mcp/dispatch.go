package mcp

import (
	"net/http"
)

// Methods of the 2025 and 2026 protocol eras that types.go does not name.
const (
	MethodResourcesTemplatesList = "resources/templates/list"
	MethodResourcesSubscribe     = "resources/subscribe"
	MethodResourcesUnsubscribe   = "resources/unsubscribe"
	MethodCompletionComplete     = "completion/complete"
	MethodLoggingSetLevel        = "logging/setLevel"
	MethodTasksGet               = "tasks/get"
	MethodTasksResult            = "tasks/result"
	MethodTasksList              = "tasks/list"
	MethodTasksCancel            = "tasks/cancel"
	MethodProgress               = "notifications/progress"
	MethodRootsListChanged       = "notifications/roots/list_changed"
	MethodServerDiscover         = "server/discover"
	MethodSubscriptionsListen    = "subscriptions/listen"
)

// methodHandler serves one JSON-RPC method.
type methodHandler func(s *Server, w http.ResponseWriter, r *http.Request, msg *Message)

// methodTable maps every method of both eras to its handler. A method with
// no handler of its own answers -32601, as an unknown one does. Handlers
// change in their own files; this table stays fixed.
var methodTable = map[string]methodHandler{
	MethodInitialize: (*Server).handleInitialize,
	MethodPing: func(s *Server, w http.ResponseWriter, _ *http.Request, msg *Message) {
		s.handlePing(w, msg)
	},
	MethodInitialized:            (*Server).handleNotification,
	MethodCancelled:              (*Server).handleNotification,
	MethodProgress:               (*Server).handleNotification,
	MethodRootsListChanged:       (*Server).handleNotification,
	MethodToolsList:              (*Server).handleToolsList,
	MethodToolsCall:              (*Server).handleToolsCall,
	MethodResourcesList:          (*Server).handleResourcesList,
	MethodResourcesRead:          (*Server).handleResourcesRead,
	MethodResourcesTemplatesList: (*Server).handleResourceTemplatesList,
	MethodResourcesSubscribe:     (*Server).handleResourcesSubscribe,
	MethodResourcesUnsubscribe:   (*Server).handleResourcesSubscribe,
	MethodPromptsList:            (*Server).handlePromptsList,
	MethodPromptsGet:             (*Server).handlePromptsGet,
	MethodCompletionComplete:     (*Server).handleCompletion,
	MethodLoggingSetLevel:        (*Server).handleSetLevel,
	MethodTasksGet:               (*Server).handleTasks,
	MethodTasksResult:            (*Server).handleTasks,
	MethodTasksList:              (*Server).handleTasks,
	MethodTasksCancel:            (*Server).handleTasks,
	MethodServerDiscover:         (*Server).handleDiscover,
	MethodSubscriptionsListen:    (*Server).handleListen,
}

// dispatch routes msg to its method handler.
func (s *Server) dispatch(w http.ResponseWriter, r *http.Request, msg *Message) {
	if h, ok := methodTable[msg.Method]; ok {
		h(s, w, r, msg)
		return
	}
	writeMethodNotFound(w, msg)
}

// writeMethodNotFound is the answer to a method nobody serves.
func writeMethodNotFound(w http.ResponseWriter, msg *Message) {
	writeJSONRPCError(w, msg.ID, ErrCodeMethodNotFound, "unknown method: "+msg.Method)
}
