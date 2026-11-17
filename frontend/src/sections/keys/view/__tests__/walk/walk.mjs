// Browser integration, not a managed-stack E2E flow. All API replies are fixtures.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createServer } from "vite";
const here = path.dirname(fileURLToPath(import.meta.url));
const out = path.resolve(process.argv[2] || "out/personal-key-browser");
fs.mkdirSync(out, { recursive: true });
const playwright = await import(
  pathToFileURL(path.join(process.env.PLAYWRIGHT_CORE, "index.mjs")).href
);
const server = await createServer({
  configFile: path.join(here, "vite.config.mjs"),
  cacheDir: path.join(out, ".vite"),
});
await server.listen();
const origin = server.resolvedUrls.local[0].replace(/\/$/, "");
const receipt = { fixtureOnly: true, checks: [], failures: [] };
const id = "11111111-1111-4111-8111-111111111111";
const snippets = {
  Python:
    'os.environ["FI_API_KEY"] = "YOUR_FI_API_KEY"\nos.environ["FI_SECRET_KEY"] = "YOUR_FI_SECRET_KEY"',
  TypeScript: 'process.env.FI_API_KEY = "YOUR_FI_API_KEY";',
};
const scenes = (
  process.env.WALK_SCENES ||
  "keys,settings,observe,experiment,knowledge,dataset,rows,dataset-create,simulation,gateway,model-api,omega"
).split(",");
try {
  for (const name of ["chromium", "firefox"]) {
    const browser = await playwright[name].launch({ headless: true });
    try {
      for (const scene of scenes) {
        const context = await browser.newContext({
          viewport: { width: 1280, height: 900 },
        });
        const page = await context.newPage();
        const retired = [];
        let creates = 0;
        const errors = [];
        page.on("request", (request) => {
          if (new URL(request.url()).pathname === "/accounts/keys/")
            retired.push(request.method());
        });
        page.on("pageerror", (error) => errors.push(error.message));
        await context.route("**/*", async (route) => {
          const url = new URL(route.request().url());
          if (url.origin !== origin) return route.abort();
          const reply = (result) =>
            route.fulfill({
              contentType: "application/json",
              body: JSON.stringify({ status: true, result }),
            });
          if (url.pathname.endsWith("/get_secret_keys/"))
            return reply({ table: [], metadata: { total_rows: 0 } });
          if (url.pathname.endsWith("/generate_secret_key/")) {
            creates++;
            return reply({
              key_id: id,
              key_name: "Personal fixture",
              api_key: "fixture-api-value",
              secret_key: "fixture-secret-value",
              masked_api_key: "fix****alue",
              masked_secret_key: "fix****alue",
            });
          }
          if (
            url.pathname === "/agentcc/api-keys/" &&
            route.request().method() === "POST"
          ) {
            creates++;
            return reply({
              id,
              name: "Personal gateway fixture",
              key: "fixture-gateway-value",
            });
          }
          if (url.pathname.startsWith("/agentcc/"))
            return reply({ providers: {} });
          if (url.pathname.endsWith("/project_sdk_code/"))
            return reply({
              keys: snippets,
              installation_guide: {
                Python: "pip install fi-instrumentation-otel",
                TypeScript: "npm install example",
              },
              project_add_code: {
                Python: "register()",
                TypeScript: "register();",
              },
              instruments: [],
            });
          if (url.pathname.endsWith("/create-empty-dataset/"))
            return reply({ dataset_name: "My dataset", dataset_id: id });
          if (url.pathname.endsWith("/add_rows_sdk/"))
            return reply({
              dataset: { id, name: "My dataset" },
              code: Object.fromEntries(
                [
                  "python_add_col",
                  "typescript_add_col",
                  "curl_add_col",
                  "python_add_row",
                  "typescript_add_row",
                  "curl_add_row",
                ].map((key) => [
                  key,
                  'api_key="YOUR_API_KEY"\nsecret_key="YOUR_SECRET_KEY"',
                ]),
              ),
            });
          if (url.pathname.endsWith("/sdk-code/"))
            return reply({
              installation_guide: "pip install example",
              run_test_id: id,
              sdk_code:
                '# export FI_API_KEY="<YOUR_FI_API_KEY>"\n# export FI_SECRET_KEY="<YOUR_FI_SECRET_KEY>"',
            });
          return route.continue();
        });
        try {
          await page.goto(`${origin}/?scene=${scene}`, {
            waitUntil: "domcontentloaded",
          });
          if (scene === "dataset-create") {
            await page
              .getByRole("textbox", { name: "Dataset Name" })
              .fill("My dataset");
            await page
              .getByRole("button", { name: "Next", exact: true })
              .click();
          }
          const control = page.getByRole("button", {
            name: "Create a key",
            exact: true,
          });
          await control.waitFor({ timeout: 90000 });
          assert.equal(creates, 0, `${scene}: no mint on mount`);
          assert.deepEqual(
            retired,
            [],
            `${scene}: no retired bootstrap request`,
          );
          if (["keys", "settings"].includes(scene))
            assert.equal(
              await page
                .getByText(
                  "No API keys yet. Create a personal key to use the SDK, CLI or OTLP.",
                  { exact: true },
                )
                .count(),
              1,
            );
          await control.click();
          if (scene === "omega") {
            await page
              .getByRole("button", { name: "Reveal key", exact: true })
              .click();
            assert.ok(
              (await page.locator("pre").allTextContents()).some((text) =>
                text.includes("fixture-secret-value"),
              ),
            );
          } else if (["gateway", "model-api"].includes(scene)) {
            const dialog = page.getByRole("dialog");
            await dialog
              .getByRole("textbox", { name: "Name", exact: true })
              .fill("Personal gateway fixture");
            assert.equal(creates, 0, "Opening gateway form does not mint");
            await dialog
              .getByRole("button", { name: "Create", exact: true })
              .click();
            await dialog.waitFor({ state: "detached" });
            assert.ok(
              (await page.locator("pre").allTextContents()).some((text) =>
                text.includes("fixture-gateway-value"),
              ),
            );
          } else {
            const dialog = page.getByRole("dialog");
            await dialog.getByLabel("Key name", { exact: false }).waitFor();
            assert.equal(creates, 0, `${scene}: opening form is not creation`);
            await dialog
              .getByRole("button", { name: "Create a key", exact: true })
              .click();
            await dialog
              .getByRole("button", { name: "I saved it", exact: true })
              .waitFor();
            assert.equal(
              await dialog.getByLabel("API key", { exact: true }).inputValue(),
              "fixture-api-value",
            );
            assert.equal(
              await dialog
                .getByLabel("Secret key", { exact: true })
                .inputValue(),
              "fixture-secret-value",
            );
            await dialog
              .getByRole("button", { name: "I saved it", exact: true })
              .click();
            await dialog.waitFor({ state: "detached" });
            if (["observe", "experiment", "simulation"].includes(scene))
              assert.ok(
                (await page.locator("pre").allTextContents()).some((text) =>
                  text.includes("fixture-secret-value"),
                ),
                `${scene}: created values fill the snippet`,
              );
            if (
              ["knowledge", "dataset", "rows", "dataset-create"].includes(scene)
            ) {
              await page
                .getByText('"fixture-secret-value"', { exact: false })
                .first()
                .waitFor();
            }
          }
          if (!["keys", "settings"].includes(scene)) {
            await page
              .getByRole("button", {
                name: scene === "omega" ? "Forget" : "Forget key",
                exact: true,
              })
              .click();
            await page.waitForFunction(
              () =>
                !document.body.textContent.includes("fixture-secret-value") &&
                !document.body.textContent.includes("fixture-gateway-value"),
            );
          }
          assert.equal(creates, 1, `${scene}: one explicit mint`);
          assert.deepEqual(retired, [], `${scene}: bootstrap never requested`);
          assert.deepEqual(errors, [], `${scene}: page errors`);
          receipt.checks.push({
            browser: name,
            scene,
            noMintOnMount: true,
            explicitCreates: creates,
            retiredRequests: 0,
          });
        } catch (error) {
          receipt.failures.push({
            browser: name,
            scene,
            error: error.message,
            pageErrors: errors,
          });
        } finally {
          await context.close();
        }
      }
    } finally {
      await browser.close();
    }
  }
} finally {
  await server.close();
  fs.writeFileSync(
    path.join(out, "receipt.json"),
    JSON.stringify(receipt, null, 2),
  );
}
console.log(JSON.stringify(receipt));
if (receipt.failures.length) process.exitCode = 1;
