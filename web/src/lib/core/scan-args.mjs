/** @param {{scanner: string, sinceDays: number, ats: string[], limitPerAts: number, json: boolean}} options */
export function buildScanArgs({ scanner, sinceDays, ats, limitPerAts, json }) {
  const args = [
    scanner,
    "--dry-run",
    "--since",
    String(Math.max(1, sinceDays || 7)),
    "--ats",
    ats.join(","),
    "--limit",
    String(Math.max(1, limitPerAts || 150)),
    // Avoid repeatedly scanning only the alphabetically first slice when capped.
    "--shuffle",
  ];
  if (json) args.push("--json");
  return args;
}
