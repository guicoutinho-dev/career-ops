import sys
from src.services.health_check import HealthCheckService

def main():
    service = HealthCheckService()
    report = service.run_health_check()

    print("==================================================")
    print("      AUSTRALIA JOB HUNTER — SYSTEM HEALTH        ")
    print("==================================================")
    print(f"Overall Status: {report['status']}")
    print("\nComponent Checks:")
    for k, v in report["checks"].items():
        print(f"  - {k}: {v}")

    if report["issues"]:
        print("\nDetected Issues:")
        for issue in report["issues"]:
            print(f"  [!] {issue}")
    else:
        print("\nAll systems operational. No issues detected.")
    print("==================================================")

if __name__ == "__main__":
    main()
