#!/usr/bin/env python
# Copyright (C) 2026 ZStack Machinery Test Script for CAPEv2
# This script is used for independent testing of ZStack machinery module

import logging
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from zstack.zstack_api import ZStackAPI, ZStackSessionManager
from zstack.zstack import ZStack

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)


class ZStackTestRunner:
    """Independent test runner for ZStack machinery."""

    def __init__(self, api_url: str, username: str, password: str):
        self.api_url = api_url
        self.username = username
        self.password = password
        self.api: ZStackAPI = None
        self.session: ZStackSessionManager = None

    def test_authentication(self) -> bool:
        """Test ZStack API authentication."""
        log.info("Testing ZStack authentication...")

        try:
            self.session = ZStackSessionManager(api_url=self.api_url, account_name=self.username, password=self.password)

            token = self.session.login()

            if token:
                log.info("Authentication successful, token: %s", token[:20] + "...")
                return True
            else:
                log.error("Authentication failed: no token received")
                return False

        except Exception as e:
            log.error("Authentication error: %s", e)
            return False

    def test_list_vms(self) -> bool:
        """Test listing virtual machines."""
        log.info("Testing VM listing...")

        try:
            vms = self.api.list_vms()
            log.info("Found %d VMs: %s", len(vms), vms)
            return True
        except Exception as e:
            log.error("VM listing failed: %s", e)
            return False

    def test_vm_state(self, vm_name: str) -> bool:
        """Test getting VM state."""
        log.info("Testing VM state for: %s", vm_name)

        try:
            state = self.api.get_vm_state(vm_name)
            log.info("VM %s state: %s", vm_name, state)
            return True
        except Exception as e:
            log.error("VM state check failed: %s", e)
            return False

    def test_vm_lifecycle(self, vm_name: str) -> bool:
        """Test VM start/stop lifecycle."""
        log.info("Testing VM lifecycle for: %s", vm_name)

        try:
            vm_uuid = self._get_vm_uuid_by_name(vm_name)
            if not vm_uuid:
                log.error("VM %s not found", vm_name)
                return False

            initial_state = self.api.get_vm_state(vm_uuid)
            log.info("Initial state: %s", initial_state)

            if initial_state == "Running":
                log.info("Stopping VM...")
                stop_result = self.api.stop_vm(vm_uuid)
                log.info("Stop result: %s", stop_result)

            import time

            time.sleep(5)

            current_state = self.api.get_vm_state(vm_uuid)
            log.info("Current state: %s", current_state)

            if current_state != "Running":
                log.info("Starting VM...")
                start_result = self.api.start_vm(vm_uuid)
                log.info("Start result: %s", start_result)

            return True

        except Exception as e:
            log.error("VM lifecycle test failed: %s", e)
            return False

    def _get_vm_uuid_by_name(self, name: str) -> str:
        """Get VM UUID by name."""
        all_vms = self.api.get_all_vm_instances()
        inventories = all_vms.get("inventories", [])

        for vm in inventories:
            if vm.get("name") == name:
                return vm.get("uuid")

        return None

    def run_all_tests(self, test_vm_name: str = None) -> bool:
        """Run all tests."""
        log.info("=" * 60)
        log.info("Starting ZStack Machinery Tests")
        log.info("=" * 60)

        if not self.test_authentication():
            log.error("Authentication test failed, stopping tests")
            return False

        self.api = ZStackAPI(api_url=self.api_url, auth_token=self.session.session_id)

        log.info("-" * 60)

        if not self.test_list_vms():
            log.error("VM listing test failed")

        log.info("-" * 60)

        if test_vm_name:
            if not self.test_vm_state(test_vm_name):
                log.error("VM state test failed")

            log.info("-" * 60)

            if not self.test_vm_lifecycle(test_vm_name):
                log.error("VM lifecycle test failed")

        log.info("=" * 60)
        log.info("Tests completed")
        log.info("=" * 60)

        return True

    def cleanup(self):
        """Cleanup resources."""
        if self.session:
            try:
                self.session.logout()
                log.info("Session closed")
            except Exception as e:
                log.warning("Failed to close session: %s", e)


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="ZStack Machinery Test Runner")
    parser.add_argument("--api-url", required=True, help="ZStack API URL")
    parser.add_argument("--username", required=True, help="ZStack username")
    parser.add_argument("--password", required=True, help="ZStack password")
    parser.add_argument("--test-vm", help="VM name for lifecycle testing")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logging.getLogger("urllib3").setLevel(logging.INFO)

    runner = ZStackTestRunner(api_url=args.api_url, username=args.username, password=args.password)

    try:
        success = runner.run_all_tests(test_vm_name=args.test_vm)
        sys.exit(0 if success else 1)
    finally:
        runner.cleanup()


if __name__ == "__main__":
    main()
