import test from "node:test";
import assert from "node:assert/strict";
import { buildScanArgs } from "./src/lib/core/scan-args.mjs";

test("Explore scan uses shuffled company sampling to avoid alphabetical-prefix bias", () => {
  const args = buildScanArgs({
    scanner: "scan-ats-full.mjs",
    sinceDays: 14,
    ats: ["greenhouse", "lever"],
    limitPerAts: 150,
    json: true,
  });

  assert.ok(args.includes("--dry-run"));
  assert.ok(args.includes("--shuffle"));
  assert.ok(args.includes("--json"));
  assert.deepEqual(args.slice(0, 8), [
    "scan-ats-full.mjs",
    "--dry-run",
    "--since",
    "14",
    "--ats",
    "greenhouse,lever",
    "--limit",
    "150",
  ]);
});
