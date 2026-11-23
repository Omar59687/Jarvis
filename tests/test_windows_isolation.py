from __future__ import annotations

from dataclasses import fields, replace
import subprocess
import unittest
from unittest.mock import patch

from jarvis_core.windows_isolation import (
    ExecutionAuthorizationEvidence,
    ExecutionResourceLimits,
    IsolationControlEvidence,
    WindowsSandboxHostFacts,
    assess_windows_sandbox,
)


class WindowsIsolationAssessmentTests(unittest.TestCase):
    def setUp(self):
        self.host = WindowsSandboxHostFacts(
            os_name="Windows 11",
            windows_build=22631,
            edition="Professional",
            architecture="AMD64",
            sandbox_feature_enabled=True,
            hardware_virtualization_enabled=True,
            installed_memory_mib=8192,
            available_memory_mib=8192,
            free_disk_mib=16384,
            cpu_cores=8,
        )
        self.authorization = ExecutionAuthorizationEvidence(
            explicit_snapshot_bound_approval=True,
            execute_verification_capability=True,
            workspace_authorized=True,
        )
        self.controls = IsolationControlEvidence(**{
            item.name: True for item in fields(IsolationControlEvidence)
        })
        self.limits = ExecutionResourceLimits(
            wall_seconds=300,
            memory_mib=2048,
            disk_mib=1024,
            child_processes=16,
            cpu_percent=80,
            output_bytes=500_000,
        )

    def assess(self, **overrides):
        args = {
            "authorization": self.authorization,
            "controls": self.controls,
            "limits": self.limits,
        }
        args.update(overrides)
        return assess_windows_sandbox(self.host, **args)

    def test_complete_reported_candidate_still_cannot_execute(self):
        result = self.assess()
        self.assertTrue(result.sandbox_host_candidate_available)
        self.assertTrue(result.authorization_reported)
        self.assertTrue(result.controls_reported_complete)
        self.assertFalse(result.execution_enabled)
        self.assertIn("execution_implementation_absent", result.reason_codes)

    def test_unknown_and_unsupported_host_facts_fail_closed(self):
        result = assess_windows_sandbox(WindowsSandboxHostFacts())
        self.assertFalse(result.sandbox_host_candidate_available)
        self.assertIn("unsupported_platform", result.reason_codes)
        self.assertIn("unsupported_windows_edition", result.reason_codes)
        self.assertIn("sandbox_feature_not_confirmed_enabled", result.reason_codes)
        self.assertFalse(result.execution_enabled)

    def test_home_edition_and_missing_windows_features_are_refused(self):
        host = WindowsSandboxHostFacts(
            os_name="Windows 11", windows_build=22631, edition="Home", architecture="AMD64",
            sandbox_feature_enabled=False, hardware_virtualization_enabled=False,
            installed_memory_mib=8192,
            available_memory_mib=8192, free_disk_mib=8192, cpu_cores=8,
        )
        result = assess_windows_sandbox(host)
        self.assertIn("unsupported_windows_edition", result.reason_codes)
        self.assertIn("sandbox_feature_not_confirmed_enabled", result.reason_codes)
        self.assertIn("hardware_virtualization_not_confirmed_enabled", result.reason_codes)
        self.assertFalse(result.sandbox_host_candidate_available)
        self.assertFalse(result.execution_enabled)

    def test_non_windows_and_insufficient_host_resources_are_refused(self):
        host = WindowsSandboxHostFacts(
            os_name="Linux", windows_build=22631, edition="Pro", architecture="AMD64",
            sandbox_feature_enabled=True, hardware_virtualization_enabled=True,
            installed_memory_mib=2048,
            available_memory_mib=2048, free_disk_mib=512, cpu_cores=1,
        )
        result = assess_windows_sandbox(host)
        self.assertIn("unsupported_platform", result.reason_codes)
        self.assertIn("insufficient_or_unknown_host_memory", result.reason_codes)
        self.assertIn("insufficient_or_unknown_free_disk", result.reason_codes)
        self.assertIn("insufficient_or_unknown_cpu_cores", result.reason_codes)

    def test_missing_user_approval_capability_or_workspace_permission_is_refused(self):
        authorization = ExecutionAuthorizationEvidence(
            explicit_snapshot_bound_approval=False,
            execute_verification_capability=False,
            workspace_authorized=False,
        )
        result = self.assess(authorization=authorization)
        self.assertFalse(result.authorization_reported)
        self.assertIn("explicit_approval_missing", result.reason_codes)
        self.assertIn("execution_capability_missing", result.reason_codes)
        self.assertIn("workspace_authorization_missing", result.reason_codes)
        self.assertFalse(result.execution_enabled)

    def test_incomplete_or_out_of_policy_resource_limits_are_refused(self):
        result = self.assess(limits=ExecutionResourceLimits(
            wall_seconds=601,
            memory_mib=2048,
            disk_mib=1024,
            child_processes=16,
            cpu_percent=80,
            output_bytes=500_000,
        ))
        self.assertIn("resource_limit_invalid_wall_seconds", result.reason_codes)
        result = self.assess(limits=None)
        self.assertIn("resource_limits_missing", result.reason_codes)

    def test_resource_budget_must_leave_host_reserve(self):
        self.host = replace(self.host, available_memory_mib=4096, free_disk_mib=2048)
        limits = ExecutionResourceLimits(
            wall_seconds=300,
            memory_mib=3073,
            disk_mib=1025,
            child_processes=16,
            cpu_percent=80,
            output_bytes=500_000,
        )
        result = self.assess(limits=limits)
        self.assertIn("resource_limit_exceeds_host_memory_reserve", result.reason_codes)
        self.assertIn("resource_limit_exceeds_host_disk_reserve", result.reason_codes)
        self.assertFalse(result.execution_enabled)

    def test_resource_limits_need_available_host_memory_fact(self):
        self.host = replace(self.host, available_memory_mib=None)
        result = self.assess()
        self.assertIn("resource_host_memory_unknown", result.reason_codes)
        self.assertFalse(result.execution_enabled)
    def test_unverified_network_credentials_and_environment_block_candidate(self):
        controls = IsolationControlEvidence(**{
            item.name: item.name not in {
                "network_disabled", "host_credentials_excluded", "environment_allowlisted",
            }
            for item in fields(IsolationControlEvidence)
        })
        result = self.assess(controls=controls)
        self.assertIn("control_unverified_network_disabled", result.reason_codes)
        self.assertIn("control_unverified_host_credentials_excluded", result.reason_codes)
        self.assertIn("control_unverified_environment_allowlisted", result.reason_codes)
        self.assertFalse(result.controls_reported_complete)
        self.assertFalse(result.execution_enabled)

    def test_missing_child_containment_or_cleanup_evidence_is_refused(self):
        controls = IsolationControlEvidence(**{
            item.name: item.name not in {"child_process_containment_verified", "cleanup_verified"}
            for item in fields(IsolationControlEvidence)
        })
        result = self.assess(controls=controls)
        self.assertIn("control_unverified_child_process_containment_verified", result.reason_codes)
        self.assertIn("control_unverified_cleanup_verified", result.reason_codes)
        self.assertFalse(result.controls_reported_complete)
        self.assertFalse(result.execution_enabled)

    def test_assessment_never_launches_processes(self):
        with patch.object(subprocess, "Popen", side_effect=AssertionError("process launched")):
            with patch.object(subprocess, "run", side_effect=AssertionError("process launched")):
                result = self.assess()
        self.assertFalse(result.execution_enabled)

    def test_host_fact_type_is_checked(self):
        with self.assertRaisesRegex(TypeError, "host facts"):
            assess_windows_sandbox(object())


if __name__ == "__main__":
    unittest.main()