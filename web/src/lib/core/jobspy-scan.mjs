export function buildJobSpyArgs(scriptPath) {
  return ["run", "--no-sync", "python", scriptPath];
}

export function parseJobSpyResult(text) {
  let payload;
  try {
    payload = JSON.parse(text);
  } catch {
    throw new Error("JobSpy output is not valid JSON");
  }
  if (!payload || typeof payload !== "object" || !Array.isArray(payload.offers)) {
    throw new Error("JobSpy output is missing its offers array");
  }
  if (!payload.sources || typeof payload.sources !== "object" || Array.isArray(payload.sources)) {
    throw new Error("JobSpy output is missing source diagnostics");
  }
  const offers = payload.offers.filter((offer) =>
    offer && typeof offer === "object" &&
    typeof offer.url === "string" && offer.url.startsWith("https://") &&
    typeof offer.company === "string" && offer.company.trim() &&
    typeof offer.title === "string" && offer.title.trim(),
  );
  return { ...payload, offers };
}
