import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

test("scanner JSON mode suppresses dotenv banners even when debug is enabled", () => {
  const scanModule = pathToFileURL(resolve(root, "scan.mjs")).href;
  const code = `process.argv.push("--json"); await import(${JSON.stringify(scanModule)});`;
  const result = spawnSync(process.execPath, ["--input-type=module", "-e", code], {
    cwd: root,
    env: { ...process.env, DOTENV_CONFIG_DEBUG: "true" },
    encoding: "utf8",
  });

  assert.equal(result.status, 0, result.stderr);
  assert.doesNotMatch(result.stdout, /injected env/);
});
