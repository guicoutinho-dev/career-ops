from pathlib import Path

import pytest

from hub.discovery.source_policy import (
    load_source_policies,
    resolve_board_policy,
    resolve_source_policy,
)


def test_unapproved_or_adzuna_policy_fails_closed_at_registry_and_catalog(tmp_path):
    example_path = Path(__file__).resolve().parent.parent / "config" / "source_policies.example.yml"
    policies = load_source_policies(example_path)

    for source_id in ("workable", "ats:greenhouse:example", "ats:lever:example", "ats:ashby:example", "adzuna_au"):
        policy = resolve_source_policy(policies, source_id)
        assert policy.publish_allowed is False

    unknown = resolve_source_policy(policies, "unknown_feed")
    assert unknown.publish_allowed is False
    assert unknown.attribution == ""

    missing_review = tmp_path / "missing_review.yml"
    missing_review.write_text(
        "sources:\n  workable:\n    publish_allowed: true\n    attribution: Jobs by Workable\n    cost_cap_aud: 0.0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="terms_reviewed_at"):
        load_source_policies(missing_review)

    adzuna_publish = tmp_path / "adzuna_publish.yml"
    adzuna_publish.write_text(
        "sources:\n  adzuna_au:\n    publish_allowed: true\n    eval_only: true\n    terms_reviewed_at: '2026-11-14'\n    cost_cap_aud: 0.0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="adzuna"):
        load_source_policies(adzuna_publish)

    approved = tmp_path / "approved.yml"
    approved.write_text(
        "sources:\n"
        "  workable:\n"
        "    publish_allowed: true\n"
        "    attribution: Jobs by Workable\n"
        "    terms_reviewed_at: '2026-11-14'\n"
        "    terms_url: https://help.workable.com/hc/en-us/articles/4420464031767-Utilizing-the-XML-Job-Feed\n"
        "    access_method: public_xml_feed\n"
        "    retention_days: 30\n"
        "    cost_cap_aud: 0.0\n"
        "    max_requests: 1\n",
        encoding="utf-8",
    )
    resolved = resolve_source_policy(load_source_policies(approved), "workable")
    assert resolved.publish_allowed is True
    assert resolved.attribution == "Jobs by Workable"
    assert resolved.max_requests == 1


def test_source_policy_rejects_incomplete_or_invalid_approval_metadata(tmp_path):
    valid = {
        "publish_allowed": True,
        "attribution": "Official Employer Career Board (Acme via Greenhouse)",
        "terms_reviewed_at": "2026-11-14",
        "terms_url": "https://acme.com.au/careers/terms",
        "access_method": "public_board_api",
        "retention_days": 30,
        "cost_cap_aud": 0.0,
        "max_requests": 10,
    }
    cases = [
        ({**valid, "terms_reviewed_at": "not-a-date"}, "terms_reviewed_at"),
        ({**valid, "terms_url": None}, "terms_url"),
        ({**valid, "terms_url": "http://127.0.0.1/terms"}, "terms_url"),
        ({**valid, "attribution": "  "}, "attribution"),
        ({**valid, "max_requests": 0}, "max_requests"),
        ({**valid, "access_method": None}, "access_method"),
        ({**valid, "retention_days": None}, "retention_days"),
        ({**valid, "cost_cap_aud": 5.0}, "cost_cap_aud"),
    ]
    for idx, (entry_payload, expected_err) in enumerate(cases):
        bad_file = tmp_path / f"bad_{idx}.yml"
        import yaml

        bad_file.write_text(yaml.safe_dump({"sources": {"ats:greenhouse:acme": entry_payload}}), encoding="utf-8")
        with pytest.raises(ValueError, match=expected_err):
            load_source_policies(bad_file)


def test_ats_policy_is_per_board_and_never_inherits_provider_approval(tmp_path):
    policies_file = tmp_path / "policies.yml"
    policies_file.write_text(
        "sources:\n"
        "  ats:greenhouse:\n"
        "    publish_allowed: true\n"
        "    attribution: Greenhouse\n"
        "    terms_url: https://www.greenhouse.io/legal\n"
        "    terms_reviewed_at: '2026-11-14'\n"
        "    access_method: public_board_api\n"
        "    retention_days: 30\n"
        "    cost_cap_aud: 0.0\n"
        "    max_requests: 10\n"
        "  ats:greenhouse:approved-cafe:\n"
        "    publish_allowed: true\n"
        "    attribution: Official Employer Career Board (approved-cafe via greenhouse)\n"
        "    terms_url: https://approved-cafe.com.au/careers\n"
        "    terms_reviewed_at: '2026-11-14'\n"
        "    access_method: public_board_api\n"
        "    retention_days: 30\n"
        "    cost_cap_aud: 0.0\n"
        "    max_requests: 10\n",
        encoding="utf-8",
    )
    policies = load_source_policies(policies_file)

    approved_board = resolve_board_policy(policies, "greenhouse", "approved-cafe")
    assert approved_board.source_id == "ats:greenhouse:approved-cafe"
    assert approved_board.publish_allowed is True

    unapproved_board = resolve_board_policy(policies, "greenhouse", "unapproved-bar")
    assert unapproved_board.source_id == "ats:greenhouse:unapproved-bar"
    assert unapproved_board.publish_allowed is False
