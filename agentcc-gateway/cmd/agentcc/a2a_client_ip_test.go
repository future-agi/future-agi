package main

// a2aTaskCarriesClientIP records that an A2A task's model call carries the
// client IP of the A2A request that started it (internal/a2a/server.go
// taskContext, internal/middleware/clientip.go WithClientIP): the task's chat
// request carries the A2A request's resolved client IP, so every IP rule of
// the pipeline (an org's ip_acl, the node's, a key's allowed_ips) judges the
// caller's address.
const a2aTaskCarriesClientIP = true
