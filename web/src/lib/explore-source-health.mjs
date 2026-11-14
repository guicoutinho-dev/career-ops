const DATASET_STATES = new Set(["ok", "stale", "empty"]);

export function buildSourceDiagnostics(sources, statsBySource = {}) {
  return sources.map((source) => {
    const stats = statsBySource?.[source];
    if (!stats || typeof stats !== "object") {
      return {
        source,
        status: "not_reported",
        datasetStatus: "unknown",
        companiesAvailable: 0,
        companiesScanned: 0,
        unreachableBoards: 0,
        matches: 0,
        postingsDroppedNoDate: 0,
      };
    }

    const datasetStatus = DATASET_STATES.has(stats.datasetStatus) ? stats.datasetStatus : "unknown";
    const unreachableBoards = Number.isFinite(stats.unreachableBoards) ? Math.max(0, stats.unreachableBoards) : 0;
    const status = datasetStatus !== "ok" ? "degraded" : unreachableBoards > 0 ? "partial" : "searched";
    return {
      source,
      status,
      datasetStatus,
      companiesAvailable: Number.isFinite(stats.companiesAvailable) ? Math.max(0, stats.companiesAvailable) : 0,
      companiesScanned: Number.isFinite(stats.companiesScanned) ? Math.max(0, stats.companiesScanned) : 0,
      unreachableBoards,
      matches: Number.isFinite(stats.matches) ? Math.max(0, stats.matches) : 0,
      postingsDroppedNoDate: Number.isFinite(stats.postingsDroppedNoDate) ? Math.max(0, stats.postingsDroppedNoDate) : 0,
    };
  });
}
