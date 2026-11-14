import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
import test from "node:test";

import { buildJobSpyArgs, parseJobSpyResult } from "./src/lib/core/jobspy-scan.mjs";

const projectRoot = resolve("..");

test("JobSpy Explore subprocess returns explicit blocked source statuses without live requests", () => {
  const result = spawnSync(
    process.env.CAREER_OPS_UV || "uv",
    buildJobSpyArgs("scripts/jobspy_explore.py"),
    {
      cwd: projectRoot,
      input: JSON.stringify({ positive: ["Farm Hand"], sinceDays: 7 }),
      encoding: "utf8",
      timeout: 120_000,
    },
  );

  assert.equal(result.error, undefined, result.error?.message);
  assert.equal(result.status, 0, result.stderr || "JobSpy Explore subprocess failed");
  const payload = parseJobSpyResult(result.stdout);
  assert.deepEqual(payload.offers, []);
  assert.equal(payload.sources.jobspy_indeed.status, "blocked");
  assert.equal(payload.sources.jobspy_linkedin.status, "blocked");
  assert.equal(payload.sources.seek_native.status, "disabled");
  assert.match(payload.sources.jobspy_indeed.error, /no request was made/i);
  assert.match(payload.sources.jobspy_linkedin.error, /no request was made/i);
  assert.match(payload.sources.seek_native.error, /disabled/i);
});
