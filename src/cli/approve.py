import sys
from typing import Optional
from src.services.approval_service import ApprovalService

def approve_application(
    app_id: str,
    confirmation_hash: Optional[str] = None,
    operator_name: str = "human:operator",
    db_path: str = "data/applications.db",
    applications_dir: str = "applications",
) -> bool:
    service = ApprovalService(db_path, applications_dir=applications_dir)
    if not confirmation_hash:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            print("Approval preview requires direct interactive terminal input/output; candidate answers were not displayed.")
            return False
        package = service.build_package(app_id)
        if not package:
            print("Approval blocked: complete review package (CV, cover letter, answers) is required.")
            return False
        package_hash = service.compute_package_hash(package)
        if not service.display_review_package(package, package_hash):
            print("Approval preview requires direct interactive terminal input/output; candidate answers were not displayed.")
            return False
        print(f"Inspect the listed PDFs and answers above, then re-run with --confirm {package_hash} in an interactive terminal.")
        return False
    success, result = service.issue_approval(app_id, confirmation_hash, operator_name)
    if not success:
        print(f"Approval blocked: {result}")
        return False
    print(f"Application '{app_id}' approved by {operator_name}.")
    print(f"Approval token: {result}")
    return True

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m src.cli.approve <app_id> [--confirm <package_hash>] [operator_name]")
        sys.exit(1)
    target_app = sys.argv[1]
    confirm = None
    operator = "human:operator"
    if "--confirm" in sys.argv:
        confirm_index = sys.argv.index("--confirm")
        if confirm_index + 1 >= len(sys.argv):
            print("Error: --confirm requires the reviewed package hash.")
            sys.exit(1)
        confirm = sys.argv[confirm_index + 1]
        extra = [arg for arg in sys.argv[2:] if arg not in ("--confirm", confirm)]
        if extra:
            operator = extra[0]
    approve_application(target_app, confirm, operator)
