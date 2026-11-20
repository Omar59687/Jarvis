import unittest

from scripts.p4_windows_acceptance import (
    _check_path_contract,
    _check_static_policy,
    run_acceptance_report,
)


class P4WindowsAcceptanceHarnessTests(unittest.TestCase):
    def test_static_policy_and_synthetic_path_contract_are_reported(self):
        self.assertEqual(_check_static_policy().status, "PASS")
        self.assertEqual(_check_path_contract().status, "PASS")

    def test_report_uses_explicit_statuses_and_never_claims_containment_ready(self):
        report = run_acceptance_report()
        allowed = {"PASS", "FAIL", "SKIPPED", "UNVERIFIED"}
        self.assertTrue(all(item["status"] in allowed for item in report["checks"]))
        self.assertEqual(report["overall"], "NOT_READY")
        self.assertGreater(report["totals"]["UNVERIFIED"], 0)
        names = {item["name"] for item in report["checks"]}
        self.assertTrue({
            "process_tree_timeout_and_cancellation",
            "network_and_loopback_isolation",
            "unauthorized_host_writes_and_credentials",
            "interrupted_job_cleanup",
        } <= names)

    @unittest.skipUnless(__import__("os").name != "nt", "Linux simulation must not be reported as NTFS evidence")
    def test_non_windows_host_marks_windows_filesystem_semantics_unverified(self):
        report = run_acceptance_report()
        statuses = {item["name"]: item["status"] for item in report["checks"]}
        for name in (
            "windows_symlink_and_reparse_points",
            "windows_hardlink_fixture",
            "windows_junction_fixture",
            "windows_ads_fixture",
            "windows_case_collision",
            "windows_acl_boundary",
        ):
            self.assertEqual(statuses[name], "UNVERIFIED")


if __name__ == "__main__":
    unittest.main()
