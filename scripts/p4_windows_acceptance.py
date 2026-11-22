"""Report P4 policy probes and Windows-only isolation checks.

This is a diagnostic harness, not an agent runner or proof that Windows Sandbox,
Hyper-V, or any other OS boundary contains an adversarial process.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from typing import Literal

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jarvis_core.coding_agents import (
    GRANTABLE_CODING_CAPABILITIES,
    CodingCapability,
    CodingTaskService,
)
from jarvis_core.developer import _path_has_link_component


Status = Literal["PASS", "FAIL", "SKIPPED", "UNVERIFIED"]


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    evidence: str


def _check_static_policy() -> Check:
    forbidden = {
        CodingCapability.CREATE_ISOLATED_WORKTREE,
        CodingCapability.MODIFY_TASK_WORKTREE,
        CodingCapability.EXECUTE_VERIFICATION,
        CodingCapability.REQUEST_NETWORK_ACCESS,
        CodingCapability.CREATE_COMMIT,
        CodingCapability.PUSH_BRANCH,
        CodingCapability.OPEN_PULL_REQUEST,
        CodingCapability.MERGE_OR_DEPLOY,
    }
    passed = not (forbidden & GRANTABLE_CODING_CAPABILITIES)
    return Check("deny_by_default_capabilities", "PASS" if passed else "FAIL",
                 "write, process, network, and external-side-effect grants are unavailable"
                 if passed else "one or more forbidden capabilities are grantable")


def _check_path_contract() -> Check:
    # Exercise the P4 proposal-path validator directly; all paths are synthetic.
    rejected = ("../escape.py", "C:/escape.py", "src\\escape.py", "src/file.py:stream",
                "src/NUL.py", "src/trailing. ")
    try:
        CodingTaskService._proposal_path("src/safe.py")
        for value in rejected:
            try:
                CodingTaskService._proposal_path(value)
            except ValueError:
                continue
            return Check("synthetic_path_policy", "FAIL", f"accepted unsafe path: {value!r}")
    except Exception as exc:  # Report a stable diagnostic instead of hiding harness errors.
        return Check("synthetic_path_policy", "FAIL", f"validator raised {type(exc).__name__}")
    return Check("synthetic_path_policy", "PASS",
                 "safe source path accepted; traversal, drive, backslash, ADS, and device paths rejected")


def _fixture_git(root: Path, args: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run fixed Git fixture commands without importing user/global Git config."""
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR") if key in os.environ}
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "cat",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
    })
    hooks = root.parent / "empty-hooks"
    hooks.mkdir(exist_ok=True)
    command = ["git", "-c", f"core.hooksPath={hooks}", "-c", "core.fsmonitor=false",
               "-c", "core.pager=cat"]
    if args and args[0] == "init":
        template = root.parent / "empty-template"
        template.mkdir(exist_ok=True)
        args = [*args, f"--template={template}"]
    return subprocess.run([*command, "-C", str(root), *args], env=env,
                          timeout=10, **kwargs)


def _check_synthetic_git_identity() -> Check:
    try:
        with tempfile.TemporaryDirectory(prefix="jarvis-p4-identity-") as directory:
            root = Path(directory)
            commands = (
                ["init", "-q"],
                ["config", "user.name", "Jarvis P4 Fixture"],
                ["config", "user.email", "p4-fixture@example.invalid"],
            )
            for command in commands:
                _fixture_git(root, command, check=True, stdout=subprocess.DEVNULL,
                             stderr=subprocess.PIPE)
            (root / "synthetic.py").write_text("def fixture(): return 1\n", encoding="utf-8")
            _fixture_git(root, ["add", "synthetic.py"], check=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            _fixture_git(root, ["commit", "--no-verify", "-qm", "synthetic base"], check=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            first = _fixture_git(root, ["rev-parse", "HEAD"], check=True,
                                 capture_output=True, text=True).stdout.strip()
            second = _fixture_git(root, ["rev-parse", "HEAD"], check=True,
                                  capture_output=True, text=True).stdout.strip()
            clean = _fixture_git(root, ["status", "--porcelain=v1", "-z"],
                                 check=True, capture_output=True).stdout == b""
            passed = len(first) in {40, 64} and first == second and clean
            evidence = "disposable repository HEAD identity stable and worktree clean" if passed else "identity or clean-state check failed"
            return Check("synthetic_repository_identity", "PASS" if passed else "FAIL", evidence)
    except (OSError, subprocess.SubprocessError) as exc:
        return Check("synthetic_repository_identity", "FAIL",
                     f"synthetic Git fixture failed: {type(exc).__name__}")


def _windows_filesystem_checks() -> list[Check]:
    if os.name != "nt":
        return [Check(name, "UNVERIFIED", "requires a Windows host and NTFS fixture") for name in (
            "windows_symlink_and_reparse_points", "windows_hardlink_fixture",
            "windows_junction_fixture", "windows_ads_fixture",
            "windows_case_collision", "windows_acl_boundary",
        )]

    checks: list[Check] = []
    with tempfile.TemporaryDirectory(prefix="jarvis-p4-ntfs-") as directory:
        root = Path(directory)
        inside = root / "repo"
        inside.mkdir()
        outside = root / "outside"
        outside.mkdir()
        (outside / "fixture.txt").write_text("synthetic only\n", encoding="utf-8")

        link = inside / "linked.txt"
        try:
            link.symlink_to(outside / "fixture.txt")
            detected = _path_has_link_component(link, inside)
            checks.append(Check("windows_symlink_and_reparse_points",
                                "PASS" if detected else "FAIL",
                                "synthetic symlink rejected by path-component guard" if detected
                                else "synthetic symlink was not rejected"))
        except (OSError, NotImplementedError):
            checks.append(Check("windows_symlink_and_reparse_points", "SKIPPED",
                                "symlink creation unavailable for this account or policy"))
        checks.append(Check("windows_junction_fixture", "UNVERIFIED",
                            "junction creation and open-time race behavior need the Windows owner run"))

        original = outside / "hardlink-target.txt"
        hardlink = inside / "hardlink.txt"
        try:
            _write_fixture(original)
            os.link(original, hardlink)
            link_count = os.stat(hardlink, follow_symlinks=False).st_nlink
            checks.append(Check("windows_hardlink_fixture",
                                "PASS" if link_count >= 2 else "FAIL",
                                f"synthetic hard-link count={link_count}; this verifies fixture creation only"))
        except OSError:
            checks.append(Check("windows_hardlink_fixture", "SKIPPED",
                                "hard-link creation unavailable for this filesystem or account"))

        base_file = inside / "ads-base.txt"
        base_file.write_text("base stream\n", encoding="utf-8")
        try:
            stream = Path(str(base_file) + ":jarvis_p4_probe")
            stream.write_text("synthetic stream\n", encoding="utf-8")
            exists = stream.read_text(encoding="utf-8") == "synthetic stream\n"
            checks.append(Check("windows_ads_fixture", "PASS" if exists else "FAIL",
                                "synthetic alternate stream created and read; no host data touched"))
        except OSError:
            checks.append(Check("windows_ads_fixture", "SKIPPED",
                                "alternate data stream probe unavailable"))

        first = inside / "CaseProbe.py"
        second = inside / "caseprobe.py"
        try:
            first.write_text("first\n", encoding="ascii", errors="strict")
            with second.open("x", encoding="ascii"):
                pass
            checks.append(Check("windows_case_collision", "FAIL",
                                "filesystem allowed two names differing only by case"))
        except FileExistsError:
            checks.append(Check("windows_case_collision", "PASS",
                                "exclusive create rejected the case-only alias"))
        except OSError:
            checks.append(Check("windows_case_collision", "SKIPPED",
                                "case-collision probe unavailable"))

    checks.append(Check("windows_acl_boundary", "UNVERIFIED",
                        "a disposable fixture ACL does not establish an OS sandbox boundary"))
    return checks


def _write_fixture(path: Path) -> Path:
    path.write_text("synthetic hard-link target\n", encoding="utf-8")
    return path


def run_acceptance_report() -> dict[str, object]:
    checks = [
        _check_static_policy(),
        _check_path_contract(),
        _check_synthetic_git_identity(),
        *_windows_filesystem_checks(),
    ]
    checks.extend(Check(name, "UNVERIFIED", reason) for name, reason in (
        ("git_hooks_and_external_filters", "no P4 runner exists; runtime behavior is not exercised"),
        ("process_tree_timeout_and_cancellation", "no OS-isolated process runner or Job Object is implemented"),
        ("network_and_loopback_isolation", "no agent network policy or sandbox is implemented"),
        ("unauthorized_host_writes_and_credentials", "no adversarial process is run against the host"),
        ("interrupted_job_cleanup", "current tasks are metadata-only and no external job exists"),
    ))
    totals = {status: sum(check.status == status for check in checks)
              for status in ("PASS", "FAIL", "SKIPPED", "UNVERIFIED")}
    return {
        "schema_version": 1,
        "host": {"system": platform.system(), "release": platform.release(),
                 "python": platform.python_version(), "windows_native": os.name == "nt"},
        "overall": "NOT_READY" if totals["FAIL"] or totals["UNVERIFIED"] else "READY_FOR_REVIEW",
        "totals": totals,
        "checks": [asdict(check) for check in checks],
        "warning": "PASS means only that the named synthetic check passed; this report does not certify OS containment.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="optional JSON report path")
    args = parser.parse_args()
    report = run_acceptance_report()
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 1 if report["overall"] == "NOT_READY" else 0


if __name__ == "__main__":
    raise SystemExit(main())
