const SETTLED_SCAN_OUTCOMES = new Set(["empty-current", "empty-loose", "degraded", "failed"]);

export function getExploreOutcomePlacement(phase) {
  return SETTLED_SCAN_OUTCOMES.has(phase) ? "above-filters" : null;
}
