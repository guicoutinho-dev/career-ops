import sys
import argparse
from src.services.form_preparer import ApplicationPreparer

def main():
    parser = argparse.ArgumentParser(description="Assemble pre-submission package and checklist for an application.")
    parser.add_argument("app_id", type=str, help="Application ID to prepare (e.g. app_006)")
    args = parser.parse_args()

    preparer = ApplicationPreparer()
    print("==================================================")
    print("    AUSTRALIA JOB HUNTER — APPLICATION PREPARATION")
    print("==================================================")
    print(f"Preparing submission package for: {args.app_id}...")

    try:
        res = preparer.assemble_submission_package(args.app_id)
        print("\n[OK] Package assembled successfully!")
        print(f"  - Package Directory : {res['package_dir']}")
        print(f"  - Checklist Path    : {res['checklist_path']}")
        print(f"  - Answers JSON      : {res['answers_path']}")
        print(f"  - CV PDF Attached   : {res['cv_path'] or 'None'}")
        print(f"  - Cover Letter PDF  : {res['cover_letter_path'] or 'None'}")
        print("\nNext Action: Review checklist.md and issue approval when ready:")
        print(f"  python -m src.cli.approve {args.app_id} \"<Your Name>\"")
    except Exception as e:
        print(f"\n[FAIL] Error preparing package: {e}")
        sys.exit(1)
    print("==================================================")

if __name__ == "__main__":
    main()
