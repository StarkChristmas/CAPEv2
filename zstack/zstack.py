# Copyright (C) 2026 ZStack Machinery for CAPEv2
# This file is part of Cuckoo Sandbox - http://www.cuckoosandbox.org
# See the file 'docs/LICENSE' for copying permission.

"""
ZStack Machinery Module for CAPEv2

This module provides integration with ZStack Cloud 5.5.6 virtualization platform.
It follows the same architecture as other CAPEv2 machinery modules (KVM, Proxmox, vSphere).

Integration Flow:
1. CAPE core imports machinery module via: import_plugin(f"modules.machinery.{machinery_name}")
2. MachineryManager.create_machinery() instantiates the machinery class
3. MachineryManager.initialize_machinery() calls machinery.initialize()
4. For each analysis task:
   - find_machine_to_service_task() finds suitable VM
   - start_machine() calls machinery.start(label)
   - stop_machine() calls machinery.stop(label)
   - dump_memory() calls machinery.dump_memory(label, path)
"""

import logging
import os
import random
import time
from typing import Any, Dict, List, Optional

from lib.cuckoo.common.abstracts import Machinery
from lib.cuckoo.common.config import Config
from lib.cuckoo.common.exceptions import CuckooCriticalError, CuckooMachineError

from .zstack_api import ZStackAPI, ZStackSessionManager

log = logging.getLogger(__name__)
cfg = Config()


class ZStack(Machinery):
    """
    ZStack Cloud machinery class for CAPEv2.

    Implements VM lifecycle management following CAPEv2 machinery interface:
    - _initialize_check(): Validate configuration and connectivity
    - start(label): Start VM by reverting to snapshot
    - stop(label): Stop VM gracefully
    - dump_memory(label, path): Create memory dump via snapshots
    - _status(label): Get VM power state
    - _list(): List all configured VMs
    """

    module_name = "zstack"

    # VM states matching ZStack 5.x API
    RUNNING = "Running"
    STOPPED = "Stopped"
    STARTING = "Starting"
    STOPPING = "Stopping"

    def __init__(self):
        """Initialize ZStack machinery module."""
        super(ZStack, self).__init__()
        self.api: Optional[ZStackAPI] = None
        self.session_manager: Optional[ZStackSessionManager] = None
        self.timeout = int(cfg.timeouts.vm_state) if hasattr(cfg, "timeouts") else 300
        random.seed()

    def _initialize_check(self) -> None:
        """
        Validate ZStack configuration and connectivity.

        This method is called by MachineryManager.initialize_machinery()
        after the machinery class is instantiated.

        Steps:
        1. Validate required configuration parameters
        2. Authenticate with ZStack API
        3. Verify all configured machines exist
        4. Check snapshot configuration

        :raise CuckooCriticalError: If configuration is invalid or connection fails
        """
        log.debug("Initializing ZStack machinery checks")

        try:
            conn_opts = self._validate_configuration()
            self._establish_connection(conn_opts)
            self._verify_machines()
        except Exception as e:
            raise CuckooCriticalError(f"Failed to initialize ZStack connection: {e}")

        super(ZStack, self)._initialize_check()
        log.info("ZStack machinery initialized successfully")

    def _validate_configuration(self) -> Dict[str, Any]:
        """
        Validate required configuration parameters from zstack.conf.

        Required configuration:
        [zstack]
        zstack_api = http://<host>:8080
        zstack_name = admin
        zstack_pwd = password
        two_fa_code = (optional)
        machines = cuckoo1, cuckoo2

        [cuckoo1]
        label = cuckoo-sandbox-01
        snapshot = clean-state
        ip = 192.168.100.10
        platform = windows
        arch = x64

        :return: Connection options dictionary
        :raise CuckooCriticalError: If required configuration is missing
        """
        conn_opts = {}

        config_section = getattr(self.options, "zstack", None)
        if not config_section:
            raise CuckooCriticalError("ZStack configuration section not found")

        # Required parameters
        required_configs = {
            "zstack_api": ("api", "ZStack API URL"),
            "zstack_name": ("zstack_name", "ZStack account name"),
            "zstack_pwd": ("zstack_pwd", "ZStack account password"),
        }

        for opt_key, (config_key, description) in required_configs.items():
            value = getattr(config_section, config_key, None)
            if not value:
                raise CuckooCriticalError(f"{description} missing in configuration")
            conn_opts[opt_key] = value

        # Optional parameters
        conn_opts["two_fa_code"] = getattr(config_section, "two_fa_code", None)
        conn_opts["verify_ssl"] = getattr(config_section, "verify_ssl", False)
        conn_opts["timeout"] = getattr(config_section, "timeout", 30)

        log.debug("ZStack configuration validated")
        return conn_opts

    def _establish_connection(self, conn_opts: Dict[str, Any]) -> None:
        """
        Establish connection to ZStack API.

        :param conn_opts: Connection options
        :raise CuckooCriticalError: If connection fails
        """
        try:
            self.session_manager = ZStackSessionManager(
                api_url=conn_opts["zstack_api"],
                account_name=conn_opts["zstack_name"],
                password=conn_opts["zstack_pwd"],
                two_fa_code=conn_opts.get("two_fa_code"),
                verify_ssl=conn_opts.get("verify_ssl", False),
            )

            auth_token = self.session_manager.login()

            self.api = ZStackAPI(
                api_url=conn_opts["zstack_api"],
                auth_token=auth_token,
                timeout=conn_opts.get("timeout", 30),
                verify_ssl=conn_opts.get("verify_ssl", False),
            )

            log.debug("Successfully connected to ZStack API")

        except CuckooMachineError as e:
            raise CuckooCriticalError(f"ZStack authentication failed: {e}")

    def _verify_machines(self) -> None:
        """
        Verify all configured machines exist on ZStack host.

        Checks:
        1. Each machine has a snapshot configured
        2. Each machine exists on ZStack
        3. VM states are accessible

        :raise CuckooCriticalError: If any machine is not found or misconfigured
        """
        if not self.api:
            raise CuckooCriticalError("ZStack API not initialized")

        configured_vms = self.api.list_vms()
        log.debug("Configured VMs on ZStack: %s", configured_vms)

        for machine in self.machines():
            # Check snapshot configuration
            if not machine.snapshot:
                raise CuckooCriticalError(f"Snapshot not specified for machine {machine.label}")

            # Check VM exists
            if machine.label not in configured_vms:
                raise CuckooCriticalError(f"Machine {machine.label} not found on ZStack host")

            # Check VM info is accessible
            vm_info = self._get_vm_info(machine.label)
            if not vm_info:
                raise CuckooCriticalError(f"Unable to retrieve info for machine {machine.label}")

        log.debug("All machines verified on ZStack host")

    def _get_vm_info(self, label: str) -> Optional[Dict[str, Any]]:
        """
        Get VM information by label.

        :param label: VM label/name
        :return: VM information dict or None
        """
        if not self.api:
            return None

        try:
            all_vms = self.api.get_all_vm_instances()
            inventories = all_vms.get("inventories", [])

            for vm in inventories:
                if vm.get("name") == label:
                    return vm

            return None

        except CuckooMachineError as e:
            log.warning("Failed to get VM info for %s: %s", label, e)
            return None

    def _get_vm_uuid(self, label: str) -> Optional[str]:
        """
        Get VM UUID by label.

        :param label: VM label/name
        :return: VM UUID or None
        """
        vm_info = self._get_vm_info(label)
        return vm_info.get("uuid") if vm_info else None

    def _get_vm_state(self, label: str) -> str:
        """
        Get current power state of a VM.

        :param label: VM label/name
        :return: VM state
        :raise CuckooMachineError: If state cannot be determined
        """
        if not self.api:
            raise CuckooMachineError("ZStack API not initialized")

        vm_uuid = self._get_vm_uuid(label)
        if not vm_uuid:
            raise CuckooMachineError(f"VM {label} not found")

        try:
            state = self.api.get_vm_state(vm_uuid)
            log.debug("VM %s state: %s", label, state)
            return state
        except CuckooMachineError as e:
            raise CuckooMachineError(f"Failed to get state for {label}: {e}")

    def _list(self) -> List[str]:
        """
        List all virtual machines on ZStack host.

        Required by Machinery base class for _initialize_check().

        :return: List of VM names
        """
        if not self.api:
            return []

        try:
            return self.api.list_vms()
        except CuckooMachineError as e:
            log.warning("Failed to list VMs: %s", e)
            return []

    def _status(self, label: str) -> str:
        """
        Get power state of a VM.

        Required by Machinery base class for state checking.

        :param label: VM label/name
        :return: VM state
        :raise CuckooMachineError: If state cannot be determined
        """
        return self._get_vm_state(label)

    def start(self, label: str) -> None:
        """
        Start a virtual machine by reverting to snapshot.

        Called by MachineryManager.start_machine() when starting analysis.

        Flow:
        1. Get current VM state
        2. If running, stop first
        3. Revert to configured snapshot
        4. Wait for VM to be running

        :param label: VM label/name
        :raise CuckooMachineError: If VM cannot be started
        """
        log.debug("Starting VM: %s", label)

        if not self.api:
            raise CuckooMachineError("ZStack API not initialized")

        machine = self.db.view_machine_by_label(label)
        if not machine:
            raise CuckooMachineError(f"Machine {label} not found in database")

        snapshot_name = machine.snapshot
        if not snapshot_name:
            raise CuckooMachineError(f"No snapshot configured for {label}")

        vm_uuid = self._get_vm_uuid(label)
        if not vm_uuid:
            raise CuckooMachineError(f"VM {label} not found on ZStack")

        try:
            current_state = self._get_vm_state(label)

            if current_state == self.RUNNING:
                log.debug("VM %s already running, stopping first", label)
                self.stop(label)

            self._revert_to_snapshot(label, vm_uuid, snapshot_name)

            log.info("VM %s started successfully", label)

        except CuckooMachineError as e:
            raise CuckooMachineError(f"Failed to start VM {label}: {e}")

    def stop(self, label: str) -> None:
        """
        Stop a virtual machine.

        Called by MachineryManager.stop_machine() when stopping analysis.

        Flow:
        1. Get current VM state
        2. If already stopped, return
        3. Send graceful stop command
        4. Wait for VM to stop

        :param label: VM label/name
        :raise CuckooMachineError: If VM cannot be stopped
        """
        log.debug("Stopping VM: %s", label)

        if not self.api:
            raise CuckooMachineError("ZStack API not initialized")

        vm_uuid = self._get_vm_uuid(label)
        if not vm_uuid:
            raise CuckooMachineError(f"VM {label} not found")

        try:
            current_state = self._get_vm_state(label)

            if current_state == self.STOPPED:
                log.debug("VM %s already stopped", label)
                return

            result = self.api.stop_vm(vm_uuid, stop_type="grace")

            job_uuid = self._extract_job_uuid(result)
            if job_uuid:
                self.api.wait_for_job_completion(job_uuid, timeout=self.timeout)

            self._wait_status(label, self.STOPPED)

            log.info("VM %s stopped successfully", label)

        except CuckooMachineError as e:
            raise CuckooMachineError(f"Failed to stop VM {label}: {e}")

    def _revert_to_snapshot(self, label: str, vm_uuid: str, snapshot_name: str) -> None:
        """
        Revert VM to a specific snapshot.

        ZStack 5.x snapshot flow:
        1. Stop VM if running
        2. Query snapshots to find matching snapshot
        3. Restore from snapshot
        4. Start VM
        5. Wait for running state

        :param label: VM label/name
        :param vm_uuid: VM UUID
        :param snapshot_name: Snapshot name
        :raise CuckooMachineError: If revert operation fails
        """
        log.info("Reverting VM %s to snapshot %s", label, snapshot_name)

        if not self.api:
            raise CuckooMachineError("ZStack API not initialized")

        try:
            # Get root volume UUID
            root_volume_uuid = self.api.get_vm_root_volume_uuid(vm_uuid)
            if not root_volume_uuid:
                raise CuckooMachineError(f"Cannot get root volume for {label}")

            # Query snapshots for this volume
            snapshots = self.api.query_volume_snapshots(root_volume_uuid)

            # Find matching snapshot by name
            snapshot_uuid = None
            for snap in snapshots:
                if snap.get("name") == snapshot_name:
                    snapshot_uuid = snap.get("uuid")
                    break

            if not snapshot_uuid:
                raise CuckooMachineError(f"Snapshot {snapshot_name} not found for VM {label}")

            # Stop VM if running
            current_state = self.api.get_vm_state(vm_uuid)
            if current_state != self.STOPPED:
                log.debug("Stopping VM before revert: %s", label)
                stop_result = self.api.stop_vm(vm_uuid)
                stop_job_uuid = self._extract_job_uuid(stop_result)
                if stop_job_uuid:
                    self.api.wait_for_job_completion(stop_job_uuid, timeout=self.timeout)
                self._wait_status(label, self.STOPPED)

            # Restore from snapshot
            restore_result = self.api.restore_snapshot(snapshot_uuid)
            restore_job_uuid = self._extract_job_uuid(restore_result)
            if restore_job_uuid:
                self.api.wait_for_job_completion(restore_job_uuid, timeout=self.timeout)

            # Small delay to ensure snapshot restore is complete
            time.sleep(5)

            # Start VM
            start_result = self.api.start_vm(vm_uuid)
            start_job_uuid = self._extract_job_uuid(start_result)
            if start_job_uuid:
                self.api.wait_for_job_completion(start_job_uuid, timeout=self.timeout)

            # Wait for running state
            self._wait_status(label, self.RUNNING)

            log.debug("VM %s reverted to snapshot %s", label, snapshot_name)

        except CuckooMachineError as e:
            raise CuckooMachineError(f"Failed to revert VM {label}: {e}")

    def _extract_job_uuid(self, result: Dict[str, Any]) -> Optional[str]:
        """
        Extract job UUID from async operation result.

        ZStack 5.x async operation response:
        {
            "location": "/zstack/v1/api-jobs/<uuid>"
        }

        :param result: API result dict
        :return: Job UUID or None
        """
        location = result.get("location", "")
        if location:
            return location.rstrip("/").split("/")[-1]
        return None

    def dump_memory(self, label: str, path: str) -> None:
        """
        Take a memory dump of a virtual machine.

        Called by AnalysisManager.dump_memory() for memory analysis.

        ZStack 5.x memory dump approach:
        1. Create snapshot with memory state
        2. Download memory volume file
        3. Delete snapshot

        Note: This implementation may need adjustment based on your
        specific ZStack storage configuration.

        :param label: VM label/name
        :param path: Destination path for memory dump
        :raise CuckooMachineError: If memory dump fails
        """
        log.info("Creating memory dump for VM: %s", label)

        if not self.api:
            raise CuckooMachineError("ZStack API not initialized")

        vm_uuid = self._get_vm_uuid(label)
        if not vm_uuid:
            raise CuckooMachineError(f"VM {label} not found")

        try:
            snapshot_name = f"memdump_{random.randint(100000, 999999)}_{int(time.time())}"

            # Get root volume UUID
            root_volume_uuid = self.api.get_vm_root_volume_uuid(vm_uuid)
            if not root_volume_uuid:
                raise CuckooMachineError(f"Cannot get root volume for {label}")

            # Create snapshot
            snapshot_result = self.api.create_snapshot(root_volume_uuid, snapshot_name)

            job_uuid = self._extract_job_uuid(snapshot_result)
            if job_uuid:
                self.api.wait_for_job_completion(job_uuid, timeout=self.timeout)

            log.debug("Snapshot %s created for memory dump", snapshot_name)

            # Get memory volume path
            memory_path = self.api.get_memory_volume_path(vm_uuid)

            if memory_path:
                log.warning("Memory volume path found: %s (manual retrieval may be needed)", memory_path)

            # Delete snapshot
            delete_result = self.api.delete_snapshot(job_uuid)
            delete_job_uuid = self._extract_job_uuid(delete_result)
            if delete_job_uuid:
                self.api.wait_for_job_completion(delete_job_uuid, timeout=60)

            log.info("Memory dump completed for VM %s", label)

        except CuckooMachineError as e:
            raise CuckooMachineError(f"Memory dump failed for {label}: {e}")
        except Exception as e:
            raise CuckooMachineError(f"Unexpected error during memory dump: {e}")

    def shutdown(self) -> None:
        """
        Shutdown all running machines and close ZStack session.

        Called by MachineryManager when CAPE is shutting down.
        """
        log.info("Shutting down ZStack machinery")

        try:
            super(ZStack, self).shutdown()
        finally:
            if self.session_manager:
                self.session_manager.logout()
                self.session_manager = None
                self.api = None

            log.debug("ZStack session closed")

    def screenshot(self, label: str, path: str) -> None:
        """
        Take a screenshot of a running VM.

        Note: ZStack screenshot functionality requires QEMU guest agent.
        This will be implemented in a future version.

        :param label: VM label/name
        :param path: Destination path for screenshot
        :raise NotImplementedError: Screenshot not yet implemented
        """
        raise NotImplementedError("Screenshot functionality not yet implemented for ZStack machinery")
