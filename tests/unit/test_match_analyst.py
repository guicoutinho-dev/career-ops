import unittest
from src.services.match_analyst import MatchAnalyst

class TestMatchAnalyst(unittest.TestCase):
    def setUp(self):
        self.analyst = MatchAnalyst()

    def test_hard_blocker_citizenship(self):
        desc = "Exciting SAP Developer role at Defence. Must be an Australian citizen to obtain clearance."
        res = self.analyst.evaluate_job(
            job_id="job_defence",
            title="SAP ABAP Specialist",
            description=desc,
            location="Canberra, ACT",
            employer="Commonwealth Defence",
            role_track="sap"
        )
        self.assertEqual(res.total_score, 0.0)
        self.assertEqual(res.recommendation, "AUTO_DISQUALIFY")
        self.assertTrue(any("Citizenship" in b for b in res.hard_blockers))

    def test_hard_blocker_nv1_clearance(self):
        desc = "Seeking Senior ABAP Developer. Candidate must have active NV1 clearance."
        res = self.analyst.evaluate_job(
            job_id="job_nv1",
            title="Senior SAP ABAP Developer",
            description=desc,
            location="Sydney, NSW",
            employer="Federal Agency",
            role_track="sap"
        )
        self.assertEqual(res.total_score, 0.0)
        self.assertEqual(res.recommendation, "AUTO_DISQUALIFY")
        self.assertTrue(any("NV1" in b for b in res.hard_blockers))

    def test_high_fit_sap_contract_job(self):
        desc = """
        Leading FMCG client in Melbourne is looking for a SAP ABAP Consultant for a 12-month contract.
        Key skills: SAP ABAP, S/4HANA migration, CDS Views, AMDP, OData via SAP Gateway, and BTP experience.
        Rate: $850 - $1000 per day. Open to candidates with independent work rights (Working Holiday Visa eligible).
        """
        res = self.analyst.evaluate_job(
            job_id="job_high_fit",
            title="SAP ABAP Consultant - 12 Month Contract",
            description=desc,
            location="Melbourne, VIC",
            employer="Premier Tech Consulting",
            role_track="sap"
        )
        self.assertGreaterEqual(res.total_score, 75.0)
        self.assertGreaterEqual(res.scaled_score_5, 4.0)
        self.assertEqual(res.recommendation, "AUTO_QUALIFY")
        self.assertEqual(len(res.hard_blockers), 0)
        self.assertIn("SAP ABAP Core", res.strengths)
        self.assertIn("SAP S/4HANA", res.strengths)

    def test_high_fit_job_without_explicit_whv_acceptance_needs_review(self):
        desc = """
        SAP ABAP Consultant for a 6-month contract in Melbourne.
        Key skills: SAP ABAP, S/4HANA, CDS Views, OData and BTP.
        Rate: $900 per day. Candidates with temporary work rights are welcome.
        """
        res = self.analyst.evaluate_job(
            job_id="job_whv_unconfirmed",
            title="SAP ABAP Consultant",
            description=desc,
            location="Melbourne, VIC",
            employer="Premier Tech Consulting",
            role_track="sap"
        )
        self.assertEqual(res.recommendation, "NEEDS_REVIEW")
        self.assertTrue(any("does not explicitly confirm" in gap for gap in res.gaps))

    def test_job_explicitly_rejecting_whv_is_disqualified(self):
        desc = """
        SAP ABAP Consultant for a 6-month contract in Melbourne.
        Key skills: SAP ABAP, S/4HANA, CDS Views, OData and BTP.
        Working Holiday Visa (subclass 462) is not accepted.
        """
        res = self.analyst.evaluate_job(
            job_id="job_whv_rejected",
            title="SAP ABAP Consultant",
            description=desc,
            location="Melbourne, VIC",
            employer="Premier Tech Consulting",
            role_track="sap"
        )
        self.assertEqual(res.recommendation, "AUTO_DISQUALIFY")
        self.assertTrue(any("does not accept Working Holiday Visa" in blocker for blocker in res.hard_blockers))

    def test_acceptance_of_subclass_417_alone_does_not_verify_subclass_462(self):
        desc = """
        SAP ABAP Consultant for a 6-month contract in Melbourne.
        Key skills: SAP ABAP, S/4HANA, CDS Views, OData and BTP.
        Working Holiday Visa subclass 417 accepted; subclass 462 not mentioned.
        """
        res = self.analyst.evaluate_job(
            job_id="job_whv_417_only",
            title="SAP ABAP Consultant",
            description=desc,
            location="Melbourne, VIC",
            employer="Premier Tech Consulting",
            role_track="sap"
        )
        self.assertEqual(res.recommendation, "NEEDS_REVIEW")
        self.assertTrue(any("does not explicitly confirm" in gap for gap in res.gaps))

    def test_low_fit_unrelated_job(self):
        desc = "Java Backend Microservices developer needed for Spring Boot and Kubernetes microservices."
        res = self.analyst.evaluate_job(
            job_id="job_low_fit",
            title="Java Backend Engineer",
            description=desc,
            location="Perth, WA",
            employer="Fintech Corp",
            role_track="sap"
        )
        self.assertLess(res.total_score, 60.0)
        self.assertEqual(res.recommendation, "AUTO_DISQUALIFY")

    def test_operational_track(self):
        desc = "Immediate start warehouse storeperson wanted for pick & pack, inventory and dispatch operations in Sydney."
        res = self.analyst.evaluate_job(
            job_id="job_op",
            title="Warehouse Storeperson",
            description=desc,
            location="Sydney, NSW",
            employer="Logistics AU",
            role_track="operational"
        )
        self.assertGreaterEqual(res.total_score, 70.0)
        self.assertIn("Warehouse Operations", res.strengths)

if __name__ == "__main__":
    unittest.main()
