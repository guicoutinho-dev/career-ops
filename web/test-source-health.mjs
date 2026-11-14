import assert from "node:assert/strict";
import test from "node:test";
import { buildSourceDiagnostics } from "./src/lib/explore-source-health.mjs";

test("buildSourceDiagnostics keeps per-ATS counts and flags unreachable boards", () => {
  assert.deepEqual(
    buildSourceDiagnostics(["greenhouse", "lever"], {
      greenhouse: {
        datasetStatus: "ok",
        companiesAvailable: 40,
        companiesScanned: 12,
        unreachableBoards: 2,
        matches: 3,
        postingsDroppedNoDate: 1,
      },
      lever: {
        datasetStatus: "ok",
        companiesAvailable: 18,
        companiesScanned: 18,
        unreachableBoards: 0,
        matches: 0,
        postingsDroppedNoDate: 0,
      },
    }),
    [
      {
        source: "greenhouse",
        status: "partial",
        datasetStatus: "ok",
        companiesAvailable: 40,
        companiesScanned: 12,
        unreachableBoards: 2,
        matches: 3,
        postingsDroppedNoDate: 1,
      },
      {
        source: "lever",
        status: "searched",
        datasetStatus: "ok",
        companiesAvailable: 18,
        companiesScanned: 18,
        unreachableBoards: 0,
        matches: 0,
        postingsDroppedNoDate: 0,
      },
    ],
  );
});

test("buildSourceDiagnostics distinguishes stale and unreported datasets", () => {
  const result = buildSourceDiagnostics(["ashby", "workday"], {
    ashby: {
      datasetStatus: "stale",
      companiesAvailable: 5,
      companiesScanned: 5,
      unreachableBoards: 0,
      matches: 0,
      postingsDroppedNoDate: 0,
    },
  });
  assert.equal(result[0].status, "degraded");
  assert.equal(result[1].status, "not_reported");
});
