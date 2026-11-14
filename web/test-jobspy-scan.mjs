import assert from "node:assert/strict";
import test from "node:test";
import { buildJobSpyArgs, parseJobSpyResult } from "./src/lib/core/jobspy-scan.mjs";

test("JobSpy runs through uv without syncing packages or writing data", () => {
  assert.deepEqual(buildJobSpyArgs("scripts/jobspy_explore.py"), [
    "run",
    "--no-sync",
    "python",
    "scripts/jobspy_explore.py",
  ]);
});

test("JobSpy output parser requires source diagnostics and normalized offers", () => {
  const parsed = parseJobSpyResult(JSON.stringify({
    offers: [{ url: "https://example.com/1", title: "Farm Hand", company: "Farm" }],
    sources: { jobspy_indeed: { status: "searched", matches: 1 } },
  }));
  assert.equal(parsed.offers.length, 1);
  assert.equal(parsed.sources.jobspy_indeed.status, "searched");
});

test("JobSpy output parser rejects malformed JSON and missing source stats", () => {
  assert.throws(() => parseJobSpyResult("not json"), /JSON/);
  assert.throws(() => parseJobSpyResult('{"offers":[]}'), /source diagnostics/);
});
