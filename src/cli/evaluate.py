import sys
import argparse
from src.services.batch_evaluator import evaluate_pending_jobs
from src.services.match_analyst import MatchAnalyst

def main():
    parser = argparse.ArgumentParser(description="Evaluate pending jobs against the candidate profile.")
    parser.add_argument("--limit", type=int, default=10, help="Number of pending jobs to evaluate")
    parser.add_argument("--track", type=str, default="sap", choices=["sap", "operational"], help="Target role track")
    args = parser.parse_args()

    print(f"Starting Match Analyst evaluation (track: {args.track}, limit: {args.limit})...")
    evaluations = evaluate_pending_jobs(limit=args.limit)

    print("\n==================================================")
    print("           MATCH ANALYST EVALUATION REPORT        ")
    print("==================================================")
    if not evaluations:
        print("No pending jobs found with status 'DISCOVERED'.")
    for res in evaluations:
        badge = "[PASS]" if res.recommendation == "AUTO_QUALIFY" else ("[REVIEW]" if res.recommendation == "NEEDS_REVIEW" else "[DISQ]")
        print(f"\n{badge} [{res.recommendation}] Job: {res.job_id}")
        print(f"   Score: {res.total_score}% ({res.scaled_score_5}/5.0) | Confidence: {int(res.confidence * 100)}%")
        if res.hard_blockers:
            print(f"   Hard Blockers: {', '.join(res.hard_blockers)}")
        if res.strengths:
            print(f"   Top Strengths: {', '.join(res.strengths[:3])}")
        if res.gaps:
            print(f"   Gaps / Risks: {', '.join(res.gaps[:2])}")
    print("\n==================================================")

if __name__ == "__main__":
    main()
