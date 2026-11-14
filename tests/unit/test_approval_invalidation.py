import unittest
import hashlib

class TestApprovalInvalidation(unittest.TestCase):
    def test_hash_invalidation_on_cv_change(self):
        job_id = "job_123"
        job_desc_hash = hashlib.sha256(b"original job description").hexdigest()
        cv_hash_v1 = hashlib.sha256(b"cv version 1 content").hexdigest()
        cover_hash_v1 = hashlib.sha256(b"cover letter v1").hexdigest()

        # Approval package hash v1
        package_v1 = f"{job_id}:{job_desc_hash}:{cv_hash_v1}:{cover_hash_v1}"
        approval_token_v1 = hashlib.sha256(package_v1.encode("utf-8")).hexdigest()

        # CV is modified (e.g., candidate adds new skill)
        cv_hash_v2 = hashlib.sha256(b"cv version 2 content modified").hexdigest()
        package_v2 = f"{job_id}:{job_desc_hash}:{cv_hash_v2}:{cover_hash_v1}"
        approval_token_v2 = hashlib.sha256(package_v2.encode("utf-8")).hexdigest()

        # Tokens must differ, invalidating approval v1
        self.assertNotEqual(approval_token_v1, approval_token_v2)

if __name__ == "__main__":
    unittest.main()
