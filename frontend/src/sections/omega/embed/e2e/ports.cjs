/* eslint-env node */
// The loopback origins the embed-host e2e uses, in one place. Each port can be
// moved with an environment variable when another tool on this computer holds
// it (omega dev also defaults to 4191); servers, config and specs all read here.
const port = (name, fallback) => Number(process.env[name] || fallback);

const PORTS = Object.freeze({
  parent: port("EMBED_PARENT_PORT", 3031),
  control: port("EMBED_CONTROL_PORT", 3033),
  child: port("EMBED_CHILD_PORT", 4191),
  third: port("EMBED_THIRD_PORT", 4192),
});

const PARENT = `http://localhost:${PORTS.parent}`;
const CONTROL = `http://localhost:${PORTS.control}`;
const CHILD = `http://127.0.0.1:${PORTS.child}`;
const THIRD = `http://127.0.0.1:${PORTS.third}`;

module.exports = { CHILD, CONTROL, PARENT, PORTS, THIRD };
