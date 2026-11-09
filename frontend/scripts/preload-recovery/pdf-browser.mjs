/* eslint-env node */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const deferred = () => {
  let resolve;
  const promise = new Promise((r) => {
    resolve = r;
  });
  return { promise, resolve };
};
async function waitHeld(promise) {
  let timer;
  try {
    await Promise.race([
      promise,
      new Promise((_resolve, reject) => {
        timer = setTimeout(
          () => reject(new Error("PDF chunk was not requested")),
          15000,
        );
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}
async function assertRendered(page) {
  // Real PdfPreview has finished rendering the real pdf.js page to its canvas.
  await page.waitForFunction(() => {
    const canvas = document.querySelector("canvas");
    return (
      canvas?.width > 0 &&
      getComputedStyle(canvas).display !== "none" &&
      document.body.textContent.includes("1 page")
    );
  });
}

export async function pdfScenarios(browserType, scenario, origin, root) {
  const manifest = JSON.parse(
    fs.readFileSync(path.join(root, ".vite/manifest.json"), "utf8"),
  );
  const entries = Object.entries(manifest);
  const library = entries.find(([, entry]) =>
    /^assets\/pdf-[^/]+\.js$/.test(entry.file),
  )?.[1]?.file;
  const worker = entries.find(([key]) =>
    key.endsWith("pdf.worker.min.js?url"),
  )?.[1]?.file;
  assert(library, "fixture manifest must identify the real pdf.js chunk");
  assert(worker, "fixture manifest must identify the worker URL import chunk");
  const start = `${origin}/preload-pdf/pdf-a`;
  const destination = `${origin}/preload-pdf/route-b`;

  for (const [name, file] of [
    ["library", library],
    ["worker-url", worker],
  ]) {
    await scenario(
      browserType,
      `pdf-current-${name}-recovers`,
      async (page, record) => {
        let blocked = 0;
        await page.route(`${origin}/preload-pdf/${file}`, (route) => {
          if (record.documents.length > 1) return route.continue();
          blocked += 1;
          return route.fulfill({
            status: 404,
            contentType: "text/javascript",
            body: "",
          });
        });
        await page.goto(start, { waitUntil: "domcontentloaded" });
        await assertRendered(page);
        await delay(1500);
        assert(blocked > 0, "a real dependency request must return 404");
        assert.equal(page.url(), start);
        assert.deepEqual(
          record.documents,
          [start, start],
          "exactly one document reload before the real PDF renders",
        );
      },
    );
  }

  await scenario(
    browserType,
    "pdf-persistent-404-bounded",
    async (page, record) => {
      await page.route(`${origin}/preload-pdf/${library}`, (route) =>
        route.fulfill({
          status: 404,
          contentType: "text/javascript",
          body: "",
        }),
      );
      await page.goto(start, { waitUntil: "domcontentloaded" });
      await page
        .getByText("Couldn't load this document", { exact: true })
        .waitFor();
      await delay(1500);
      assert.equal(page.url(), start);
      assert.deepEqual(
        record.documents,
        [start, start],
        "one reload then the actual PDF error state, never a loop",
      );
      assert.equal(
        await page.evaluate(() =>
          Boolean(window.__RELOADING_FOR_VITE_PRELOAD_ERROR),
        ),
        false,
      );
    },
  );

  for (const navigation of ["spa", "document"]) {
    await scenario(
      browserType,
      `pdf-navigate-away-${navigation}`,
      async (page, record) => {
        const held = deferred();
        const abort = deferred();
        const failed = deferred();
        const chunkUrl = `${origin}/preload-pdf/${library}`;
        page.on("requestfailed", (request) => {
          if (request.url() === chunkUrl) failed.resolve();
        });
        await page.route(chunkUrl, async (route) => {
          held.resolve();
          await abort.promise;
          await route.abort("aborted").catch(() => {});
        });
        await page.goto(start, { waitUntil: "domcontentloaded" });
        await waitHeld(held.promise);
        if (navigation === "spa") {
          await page
            .getByRole("link", { name: "Leave preview", exact: true })
            .click();
          await page
            .getByRole("heading", { name: "Route B remains visible" })
            .waitFor();
          abort.resolve();
        } else {
          // Abort while the departing document still reports route A's URL.
          await page.route(destination, async (route) => {
            abort.resolve();
            await delay(300);
            await route.continue().catch(() => {});
          });
          await page.goto(destination, { waitUntil: "domcontentloaded" });
        }
        await waitHeld(failed.promise);
        await page
          .getByRole("heading", { name: "Route B remains visible" })
          .waitFor();
        await delay(1500);
        assert.equal(page.url(), destination);
        assert.deepEqual(
          record.documents,
          navigation === "spa" ? [start] : [start, destination],
          "abandoned non-React import must not reload either route",
        );
      },
    );
  }
}
