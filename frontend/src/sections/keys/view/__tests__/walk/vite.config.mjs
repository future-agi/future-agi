import path from "node:path";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react-swc";
import { localMonacoPlugin } from "../../../../../../scripts/local-monaco-plugin.mjs";
const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(here, "../../../../..");
export default {
  root: here,
  publicDir: path.resolve(src, "../public"),
  envDir: false,
  configFile: false,
  logLevel: "warn",
  plugins: [react(), localMonacoPlugin()],
  define: {
    "import.meta.env.VITE_ENVIRONMENT": JSON.stringify("local"),
    "import.meta.env.VITE_HOST_API": JSON.stringify(""),
    "import.meta.env.VITE_ASSETS_API": JSON.stringify(""),
  },
  resolve: {
    alias: [
      {
        find: /^src\/auth\/hooks(?:\/use-auth-context)?$/,
        replacement: path.join(here, "context.js"),
      },
      {
        find: /^src\/contexts\/(?:OrganizationContext|WorkspaceContext)$/,
        replacement: path.join(here, "context.js"),
      },
      { find: /^src\/(.+)/, replacement: `${src}/$1` },
    ],
  },
  server: {
    host: "127.0.0.1",
    port: 0,
    fs: { allow: [path.resolve(src, "..")] },
  },
  optimizeDeps: { entries: [path.join(here, "main.jsx")] },
};
