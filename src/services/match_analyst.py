import re
import yaml
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Literal
from pydantic import BaseModel, Field

RecommendationType = Literal["AUTO_QUALIFY", "NEEDS_REVIEW", "AUTO_DISQUALIFY"]

class MatchEvaluation(BaseModel):
    job_id: str
    role_track: str
    total_score: float
    scaled_score_5: float
    confidence: float
    recommendation: RecommendationType
    dimension_scores: Dict[str, float]
    hard_blockers: List[str] = Field(default_factory=list)
    strengths: List[str] = Field(default_factory=list)
    gaps: List[str] = Field(default_factory=list)
    evidence: Dict[str, str] = Field(default_factory=dict)


WHV_TERMS = r"(?:working\s+holiday(?:\s+visa)?|whv|subclass\s*462|visa\s+(?:subclass\s*)?462)"
WHV_ACCEPTANCE_PATTERNS = [
    rf"\b(?:open\s+to|welcome|accept(?:s|ed|ing)?|eligible|consider(?:s|ed|ing)?|suitable\s+for)\b.{{0,100}}\b{WHV_TERMS}\b",
    rf"\b{WHV_TERMS}\b.{{0,40}}\b(?:accepted|welcome|eligible|considered|permitted)\b",
]
WHV_REJECTION_PATTERNS = [
    rf"\b{WHV_TERMS}\b.{{0,40}}\b(?:not\s+accepted|not\s+eligible|not\s+considered|not\s+welcome|not\s+permitted)\b",
    rf"\b(?:do\s+not|don't|does\s+not|doesn't|will\s+not|won't)\s+(?:accept|consider|hire|employ)\b.{{0,70}}\b{WHV_TERMS}\b",
]


def _has_explicit_whv_acceptance(text: str) -> bool:
    """Require positive WHV acceptance; a subclass-417-only clause is insufficient for this 462 profile."""
    clauses = re.split(r"[\n;.!?]+", text)
    for clause in clauses:
        has_417 = bool(re.search(r"\b(?:subclass\s*)?417\b", clause, re.IGNORECASE))
        has_462 = bool(re.search(r"\b(?:subclass\s*)?462\b", clause, re.IGNORECASE))
        mentions_working_holiday = bool(re.search(r"\b(?:working\s+holiday(?:\s+visa)?|whv)\b", clause, re.IGNORECASE))
        if has_417 and not has_462 and mentions_working_holiday:
            continue
        if any(re.search(pattern, clause, re.IGNORECASE) for pattern in WHV_ACCEPTANCE_PATTERNS):
            return True
    return False


class MatchAnalyst:
    """Evaluates job descriptions against the candidate's master profile and constraints."""

    HARD_BLOCKER_PATTERNS = [
        (r"\b(australian\s+citizenship\s+(is\s+)?(strictly\s+)?(required|essential|mandatory))\b", "Requires Australian Citizenship"),
        (r"\b(must\s+be\s+an\s+australian\s+citizen)\b", "Requires Australian Citizenship"),
        (r"\b(australian\s+citizens\s+only)\b", "Requires Australian Citizenship"),
        (r"\b(security\s+clearance\s*:\s*(baseline|nv1|nv2|negative\s+vetting))\b", "Requires AGSVA Security Clearance (Baseline/NV1/NV2)"),
        (r"\b(active\s+(baseline|nv1|nv2)\s+(clearance)?)\b", "Requires Active AGSVA Security Clearance"),
        (r"\b(nv1\s+clearance)\b", "Requires NV1 Security Clearance"),
        (r"\b(pr\s+or\s+citizen\s+only)\b", "Strictly PR or Citizen Only"),
    ]

    SAP_CORE_SKILLS = [
        ("abap", "SAP ABAP Core"),
        ("s/4hana", "SAP S/4HANA"),
        ("ecc", "SAP ECC"),
        ("btp", "SAP BTP"),
        ("abap cloud", "ABAP Cloud"),
        ("cds view", "CDS Views"),
        ("amdp", "AMDP Logic"),
        ("odata", "OData Services"),
        ("gateway", "SAP Gateway"),
        ("badi", "BADIs & Enhancements"),
        ("user exit", "User Exits"),
        ("alv", "ALV Reporting"),
        ("fiori", "SAP Fiori / UI5"),
        ("integration", "SAP Integrations"),
        ("debugging", "ABAP Debugging & Performance"),
    ]

    OPERATIONAL_CORE_SKILLS = [
        ("warehouse", "Warehouse Operations"),
        ("storeperson", "Storeperson / Inventory"),
        ("packing", "Pick & Pack"),
        ("dispatch", "Dispatch Logistics"),
        ("customer service", "Customer Service"),
        ("hospitality", "Food & Beverage / Hospitality"),
    ]

    # Category-specific skill catalogs (used only when `category` is passed to evaluate_job).
    # Keys match the labels produced by src.services.job_classifier.
    CATEGORY_SKILLS = {
        "88 Days Specified Work": [
            ("farm", "Farm / Agricultural work"),
            ("farmhand", "Farm hand"),
            ("harvest", "Harvest work"),
            ("fruit", "Fruit picking / packing"),
            ("pick", "Picking & packing"),
            ("pack", "Pick & Pack"),
            ("agricultur", "Agriculture"),
            ("horticultur", "Horticulture"),
            ("station", "Station / livestock work"),
            ("vineyard", "Vineyard"),
            ("solar", "Solar farm construction"),
            ("construction", "Construction labouring"),
            ("labourer", "General labouring"),
            ("meat", "Meat processing"),
            ("machinery", "Machinery / plant operation"),
            ("tractor", "Tractor operation"),
            ("irrigation", "Irrigation"),
            ("dairy", "Dairy"),
        ],
        "Office Support": [
            ("administration", "Administration"),
            ("administrator", "Administration"),
            ("data entry", "Data Entry"),
            ("reception", "Reception"),
            ("customer service", "Customer Service"),
            ("microsoft office", "Microsoft Office"),
            ("excel", "Excel"),
            ("scheduling", "Scheduling"),
            ("filing", "Filing / Records"),
            ("invoice", "Invoicing"),
            ("sap", "SAP exposure (differentiator)"),
        ],
        "Hospitality / Barista": [
            ("barista", "Barista"),
            ("coffee", "Coffee service"),
            ("espresso", "Espresso machine"),
            ("cafe", "Cafe environment"),
            ("hospitality", "Hospitality"),
            ("food and beverage", "Food & Beverage"),
            ("customer service", "Customer Service"),
            ("bartend", "Bar service"),
            ("rsa", "RSA"),
            ("kitchen", "Kitchen support"),
        ],
    }

    def __init__(self, config_dir: Path | str = Path("config")):
        self.config_dir = Path(config_dir)
        self.weights, self.thresholds = self._load_scoring_config()

    def _load_scoring_config(self) -> Tuple[Dict[str, float], Dict[str, float]]:
        scoring_path = self.config_dir / "scoring.yaml"
        default_weights = {
            "relevant_skills": 0.30,
            "mandatory_requirements": 0.20,
            "work_authorization": 0.20,
            "location_availability": 0.15,
            "compensation_settlement": 0.10,
            "listing_freshness_legitimacy": 0.05,
        }
        default_thresholds = {
            "auto_qualify": 75.0,
            "needs_review": 60.0,
            "auto_disqualify": 59.9,
        }

        if scoring_path.exists():
            try:
                data = yaml.safe_load(scoring_path.read_text(encoding="utf-8"))
                weights = data.get("weights", default_weights)
                thresholds = data.get("thresholds", default_thresholds)
                return weights, thresholds
            except Exception:
                pass
        return default_weights, default_thresholds

    def check_hard_blockers(self, text: str) -> List[str]:
        """Detects strict legal, citizenship, or clearance blockers."""
        blockers = []
        for pattern, reason in self.HARD_BLOCKER_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                if reason not in blockers:
                    blockers.append(reason)
        return blockers

    def evaluate_job(
        self,
        job_id: str,
        title: str,
        description: str,
        location: str,
        employer: str,
        role_track: str = "sap",
        publication_date: Optional[str] = None,
        category: Optional[str] = None
    ) -> MatchEvaluation:
        """
        Calculates match score, detects blockers, and provides structured evaluation.

        `category` (optional) selects a category-specific skill catalog so that valid
        non-SAP jobs (e.g. farm work for the 88-day requirement) are not auto-rejected
        for "zero overlap" with SAP/warehouse keywords.
        """
        combined_text = f"{title}\n{description}\n{location}\n{employer}"
        blockers = self.check_hard_blockers(combined_text)
        whv_rejected = any(re.search(pattern, combined_text, re.IGNORECASE) for pattern in WHV_REJECTION_PATTERNS)
        if whv_rejected:
            blockers.append("Employer explicitly does not accept Working Holiday Visa (subclass 462)")

        dim_scores = {}
        strengths = []
        gaps = []
        evidence = {}

        # 1. Hard blocker check: immediate disqualification
        if blockers:
            dim_scores = {
                "relevant_skills": 0.0,
                "mandatory_requirements": 0.0,
                "work_authorization": 0.0,
                "location_availability": 0.0,
                "compensation_settlement": 0.0,
                "listing_freshness_legitimacy": 0.0,
            }
            return MatchEvaluation(
                job_id=job_id,
                role_track=role_track,
                total_score=0.0,
                scaled_score_5=0.0,
                confidence=1.0,
                recommendation="AUTO_DISQUALIFY",
                dimension_scores=dim_scores,
                hard_blockers=blockers,
                strengths=[],
                gaps=["Violates mandatory work-authorization, citizenship, or security-clearance requirements"],
                evidence={"blocker_trigger": ", ".join(blockers)}
            )

        # 2. Relevant Skills (Weight: 30%)
        category_catalog = self.CATEGORY_SKILLS.get(category or "")
        if category_catalog:
            skill_catalog = category_catalog + self.OPERATIONAL_CORE_SKILLS
            prefix_match = True  # stems such as "harvest" must match "harvesting"
        else:
            skill_catalog = self.SAP_CORE_SKILLS if role_track == "sap" else self.OPERATIONAL_CORE_SKILLS
            prefix_match = False
        matched_skills = []
        for kw, label in skill_catalog:
            pattern = rf"\b{re.escape(kw)}" if prefix_match else rf"\b{re.escape(kw)}\b"
            if re.search(pattern, combined_text, re.IGNORECASE) and label not in matched_skills:
                matched_skills.append(label)

        if matched_skills:
            # Scaled based on match depth (up to 5 matched skills gives 100%)
            skill_ratio = min(len(matched_skills) / 5.0, 1.0)
            skills_score = 60.0 + (skill_ratio * 40.0)
            strengths.extend(matched_skills)
            evidence["matched_skills"] = ", ".join(matched_skills)
        else:
            skills_score = 0.0
            gaps.append("Zero explicit skill keyword overlap with candidate CV")

        dim_scores["relevant_skills"] = round(skills_score, 1)

        # 3. Mandatory Requirements (Weight: 20%)
        # If no skills matched the primary track, mandatory domain requirement is also failed
        if not matched_skills:
            req_score = 20.0
            gaps.append(f"Domain mismatch for {role_track.upper()} track")
        else:
            req_score = 85.0
            exp_match = re.search(r"(\d+)\+?\s*years?(\s+of)?\s+experience", combined_text, re.IGNORECASE)
            if exp_match:
                years_req = int(exp_match.group(1))
                if years_req >= 8:
                    req_score = 30.0
                    gaps.append(f"Major seniority gap: requires {years_req}+ years of experience (Candidate has ~4-5 years)")
                elif years_req > 5:
                    req_score = 50.0
                    gaps.append(f"Requires {years_req}+ years of experience (Candidate has ~4-5 years)")
                else:
                    strengths.append(f"Meets requested experience ({years_req}+ years)")
        dim_scores["mandatory_requirements"] = req_score

        # 4. Work Authorization (Weight: 20%)
        # No citizenship blocker detected; check WHV friendliness and contract duration constraints
        work_auth_score = 75.0
        has_long_contract = bool(re.search(r"\b(12|18|24|36)\s*[- ]*(months?|mths?)\b|\b(1|2)\s*[- ]*years?\s*contract\b", combined_text, re.IGNORECASE))
        has_whv_explicit = not whv_rejected and _has_explicit_whv_acceptance(combined_text)

        if has_whv_explicit:
            work_auth_score = 100.0
            strengths.append("Explicitly open to Working Holiday Visa / temporary work rights")
        else:
            work_auth_score = 40.0
            gaps.append("Job ad does not explicitly confirm acceptance of WHV subclass 462; human verification required")
            if has_long_contract:
                gaps.append("Contract duration exceeds 6-month WHV limit (requires negotiation to split or reduce to 6m)")
            elif re.search(r"\b(contract|fixed\s+term|temporary|subcontractor|6\s*[- ]*months?|3\s*[- ]*months?)\b", combined_text, re.IGNORECASE):
                strengths.append("Contract/Fixed-term structure is highly compatible with WHV 6-month work rule")
        dim_scores["work_authorization"] = work_auth_score

        # 5. Location & Availability (Weight: 15%)
        location_score = 75.0
        au_major_cities = ["sydney", "melbourne", "brisbane", "perth", "adelaide", "canberra", "remote"]
        for city in au_major_cities:
            if re.search(rf"\b{city}\b", location, re.IGNORECASE):
                location_score = 90.0
                strengths.append(f"Prime Australian tech hub location ({city.capitalize()})")
                break
        dim_scores["location_availability"] = location_score

        # 6. Compensation & Settlement (Weight: 10%)
        comp_score = 75.0
        rate_match = re.search(r"(\$\d{2,3}(?:,\d{3})*(?:\s*-\s*\$\d{2,3}(?:,\d{3})*)?(\s*(?:per\s+day|pd|\/day|\/hour|ph|per\s+hour|k|\+super)))", combined_text, re.IGNORECASE)
        if rate_match:
            comp_score = 95.0
            evidence["advertised_comp"] = rate_match.group(0).strip()
            strengths.append(f"Transparent compensation advertised: {rate_match.group(0).strip()}")
        dim_scores["compensation_settlement"] = comp_score

        # 7. Listing Freshness & Legitimacy (Weight: 5%)
        freshness_score = 85.0
        dim_scores["listing_freshness_legitimacy"] = freshness_score

        # Calculate Total Weighted Score
        total_score = sum(dim_scores[k] * self.weights.get(k, 0.0) for k in dim_scores)
        scaled_5 = round(total_score / 20.0, 1)

        # Recommendation determination
        if total_score >= self.thresholds.get("auto_qualify", 75.0):
            recommendation: RecommendationType = "AUTO_QUALIFY"
        elif total_score >= self.thresholds.get("needs_review", 60.0):
            recommendation = "NEEDS_REVIEW"
        else:
            recommendation = "AUTO_DISQUALIFY"

        if not has_whv_explicit and recommendation == "AUTO_QUALIFY":
            recommendation = "NEEDS_REVIEW"

        confidence = 0.90 if len(description) > 300 else 0.70

        return MatchEvaluation(
            job_id=job_id,
            role_track=role_track,
            total_score=round(total_score, 1),
            scaled_score_5=scaled_5,
            confidence=confidence,
            recommendation=recommendation,
            dimension_scores=dim_scores,
            hard_blockers=[],
            strengths=strengths,
            gaps=gaps,
            evidence=evidence
        )
