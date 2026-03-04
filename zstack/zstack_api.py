# Copyright (C) 2026 ZStack Machinery for CAPEv2
# This file is part of Cuckoo Sandbox - http://www.cuckoosandbox.org
# See the file 'docs/LICENSE' for copying permission.

"""
ZStack Cloud 5.5.6 API Client

Based on ZStack Cloud V5.5.6 Development Manual:
- API URL format: http://<host>:8080/zstack/v1/<resource>
- Authentication: PUT /zstack/v1/accounts/login returns session UUID
- HTTP Methods: PUT for actions, GET for queries, POST for creation, DELETE for removal
- All API calls return JSON with 'inventories' or 'result' fields
- Async operations return 'location' header with job UUID
"""

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

import requests

from lib.cuckoo.common.exceptions import CuckooMachineError

log = logging.getLogger(__name__)


class ZStackSessionManager:
    """
    Manages ZStack API session authentication with two-factor authentication support.

    ZStack 5.x API Authentication Flow:
    1. Hash password with SHA512
    2. PUT /zstack/v1/accounts/login with accountName and password
    3. Receive session UUID in response
    4. Use session UUID as Authorization header for subsequent calls
    5. Optionally logout with DELETE /zstack/v1/accounts/sessions/{uuid}
    """

    def __init__(
        self,
        api_url: str,
        account_name: str,
        password: str,
        two_fa_code: Optional[str] = None,
        verify_ssl: bool = False,
    ):
        """
        Initialize ZStack session manager.

        :param api_url: ZStack API base URL (e.g., http://192.168.1.100:8080)
        :param account_name: Account name for authentication
        :param password: Account password (will be SHA512 hashed)
        :param two_fa_code: Two-factor authentication code (optional)
        :param verify_ssl: Whether to verify SSL certificates
        """
        self.api_url = api_url.rstrip("/")
        self.account_name = account_name
        self.password = self._hash_password(password)
        self.two_fa_code = two_fa_code
        self.verify_ssl = verify_ssl
        self.headers = {"Content-Type": "application/json;charset=UTF-8"}
        self.session_id: Optional[str] = None

    @staticmethod
    def _hash_password(password: str) -> str:
        """Hash password using SHA512 as required by ZStack API."""
        return hashlib.sha512(password.encode("utf-8")).hexdigest()

    def login(self) -> str:
        """
        Authenticate with ZStack API and obtain session token.

        ZStack 5.x Login API:
        PUT /zstack/v1/accounts/login
        {
            "logInByAccount": {
                "accountName": "admin",
                "password": "<sha512_hash>"
            },
            "systemTags": ["twofatoken::<code>"]  // optional
        }

        :return: Session UUID
        :raise CuckooMachineError: If authentication fails
        """
        login_data = {"logInByAccount": {"accountName": self.account_name, "password": self.password}}

        # Add two-factor authentication if provided
        if self.two_fa_code:
            login_data["systemTags"] = [f"twofatoken::{self.two_fa_code}"]
            log.debug("Using two-factor authentication")

        url = f"{self.api_url}/zstack/v1/accounts/login"

        try:
            response = requests.put(url, headers=self.headers, json=login_data, timeout=30, verify=self.verify_ssl)
            response.raise_for_status()
            session_info = response.json()

            if "inventory" not in session_info or "uuid" not in session_info["inventory"]:
                raise CuckooMachineError("Invalid authentication response from ZStack API")

            self.session_id = session_info["inventory"]["uuid"]
            log.info("Successfully authenticated with ZStack API")
            return self.session_id

        except requests.exceptions.RequestException as e:
            raise CuckooMachineError(f"Failed to authenticate with ZStack API: {e}")
        except (KeyError, TypeError, json.JSONDecodeError) as e:
            raise CuckooMachineError(f"Invalid authentication response format: {e}")

    def logout(self) -> bool:
        """
        Logout from ZStack API session.

        ZStack 5.x Logout API:
        DELETE /zstack/v1/accounts/sessions/{uuid}

        :return: True if logout successful, False otherwise
        """
        if not self.session_id:
            return True

        url = f"{self.api_url}/zstack/v1/accounts/sessions/{self.session_id}"

        try:
            response = requests.delete(url, headers=self.headers, timeout=10, verify=self.verify_ssl)
            response.raise_for_status()
            log.debug("Successfully logged out from ZStack API")
            self.session_id = None
            return True
        except requests.exceptions.RequestException as e:
            log.warning("Failed to logout from ZStack API: %s", e)
            return False

    def __enter__(self):
        """Context manager entry - login."""
        self.login()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - logout."""
        self.logout()


class ZStackAPI:
    """
    ZStack Cloud 5.5.6 REST API client.

    Implements VM lifecycle management, snapshot operations, and volume management
    based on ZStack Cloud V5.5.6 Development Manual.
    """

    def __init__(self, api_url: str, auth_token: str, timeout: int = 30, verify_ssl: bool = False):
        """
        Initialize ZStack API client.

        :param api_url: ZStack API base URL
        :param auth_token: Authentication token (session UUID)
        :param timeout: Request timeout in seconds
        :param verify_ssl: Whether to verify SSL certificates
        """
        self.api_url = api_url.rstrip("/")
        self.auth_token = auth_token
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.headers = {"Authorization": auth_token, "Content-Type": "application/json;charset=UTF-8"}

    def _request(self, method: str, endpoint: str, params: Optional[Dict] = None, **kwargs) -> Dict[str, Any]:
        """
        Make HTTP request to ZStack API.

        :param method: HTTP method (GET, POST, PUT, DELETE)
        :param endpoint: API endpoint (e.g., /zstack/v1/vm-instances)
        :param params: Query parameters
        :param kwargs: Additional request parameters
        :return: JSON response as dict
        :raise CuckooMachineError: If request fails
        """
        url = f"{self.api_url}{endpoint}"

        try:
            response = requests.request(
                method, url, headers=self.headers, params=params, timeout=self.timeout, verify=self.verify_ssl, **kwargs
            )
            response.raise_for_status()

            # 204 No Content is expected for some DELETE operations
            if response.status_code == 204:
                return {}

            return response.json() if response.content else {}

        except requests.exceptions.Timeout:
            raise CuckooMachineError(f"Request timeout to ZStack API: {endpoint}")
        except requests.exceptions.RequestException as e:
            raise CuckooMachineError(f"ZStack API request failed: {e}")
        except (json.JSONDecodeError, ValueError) as e:
            raise CuckooMachineError(f"Invalid JSON response from ZStack API: {e}")

    # ==================== VM Instance Operations ====================

    def list_vms(self) -> List[str]:
        """
        List all virtual machines.

        ZStack 5.x API:
        GET /zstack/v1/vm-instances

        :return: List of VM names
        """
        data = self._request("GET", "/zstack/v1/vm-instances")
        inventories = data.get("inventories", [])
        return [vm["name"] for vm in inventories if "name" in vm]

    def get_vm_info(self, vm_uuid: str) -> Dict[str, Any]:
        """
        Get detailed information about a specific VM.

        ZStack 5.x API:
        GET /zstack/v1/vm-instances/{uuid}

        :param vm_uuid: VM UUID
        :return: VM information dict
        """
        data = self._request("GET", f"/zstack/v1/vm-instances/{vm_uuid}")
        inventories = data.get("inventories", [])
        return inventories[0] if inventories else {}

    def get_all_vm_instances(self) -> Dict[str, Any]:
        """
        Get all VM instances information.

        ZStack 5.x API:
        GET /zstack/v1/vm-instances

        :return: All VM instances data
        """
        return self._request("GET", "/zstack/v1/vm-instances")

    def get_vm_state(self, vm_uuid: str) -> str:
        """
        Get power state of a VM.

        VM States in ZStack 5.x:
        - Running: VM is running
        - Stopped: VM is stopped
        - Starting: VM is starting
        - Stopping: VM is stopping

        :param vm_uuid: VM UUID
        :return: VM state
        """
        vm_info = self.get_vm_info(vm_uuid)
        return vm_info.get("state", "Unknown")

    def start_vm(self, vm_uuid: str) -> Dict[str, Any]:
        """
        Start a virtual machine.

        ZStack 5.x API:
        PUT /zstack/v1/vm-instances/{uuid}/actions
        {
            "startVmInstance": {}
        }

        :param vm_uuid: VM UUID
        :return: Async job result with location header
        """
        payload = {"startVmInstance": {}}
        return self._request("PUT", f"/zstack/v1/vm-instances/{vm_uuid}/actions", json=payload)

    def stop_vm(self, vm_uuid: str, stop_type: str = "grace") -> Dict[str, Any]:
        """
        Stop a virtual machine.

        ZStack 5.x API:
        PUT /zstack/v1/vm-instances/{uuid}/actions
        {
            "stopVmInstance": {
                "type": "grace"  // or "hard"
            }
        }

        :param vm_uuid: VM UUID
        :param stop_type: Stop type (grace or hard)
        :return: Async job result
        """
        payload = {"stopVmInstance": {"type": stop_type}}
        return self._request("PUT", f"/zstack/v1/vm-instances/{vm_uuid}/actions", json=payload)

    # ==================== Volume Operations ====================

    def get_vm_root_volume_uuid(self, vm_uuid: str) -> Optional[str]:
        """
        Get root volume UUID for a VM.

        :param vm_uuid: VM UUID
        :return: Root volume UUID or None
        """
        vm_info = self.get_vm_info(vm_uuid)
        return vm_info.get("rootVolumeUuid")

    def get_vm_volumes(self, vm_uuid: str) -> List[Dict[str, Any]]:
        """
        Get all volumes attached to a VM.

        :param vm_uuid: VM UUID
        :return: List of volume information
        """
        vm_info = self.get_vm_info(vm_uuid)
        return vm_info.get("allVolumes", [])

    def get_memory_volume_path(self, vm_uuid: str) -> Optional[str]:
        """
        Get memory volume install path for a VM.

        :param vm_uuid: VM UUID
        :return: Memory volume path or None
        """
        volumes = self.get_vm_volumes(vm_uuid)
        for volume in volumes:
            if volume.get("type") == "Memory":
                return volume.get("installPath")
        return None

    # ==================== Snapshot Operations ====================

    def create_snapshot(self, volume_uuid: str, snapshot_name: str) -> Dict[str, Any]:
        """
        Create a snapshot for a volume.

        ZStack 5.x API:
        POST /zstack/v1/volume-snapshots
        {
            "params": {
                "volumeUuid": "<uuid>",
                "name": "<name>"
            }
        }

        Note: In ZStack 5.x, use /volume-snapshots not /volume-snapshots/group

        :param volume_uuid: Volume UUID
        :param snapshot_name: Snapshot name
        :return: Async job result
        """
        payload = {"params": {"volumeUuid": volume_uuid, "name": snapshot_name}}
        return self._request("POST", "/zstack/v1/volume-snapshots", json=payload)

    def get_snapshot_info(self, vm_uuid: str) -> Dict[str, Any]:
        """
        Get snapshot information for a VM.

        :param vm_uuid: VM UUID
        :return: Snapshot information
        """
        vm_info = self.get_vm_info(vm_uuid)
        all_volumes = vm_info.get("allVolumes", [])

        snapshot_info = {"root_snapshots": [], "memory_snapshots": []}

        for volume in all_volumes:
            volume_type = volume.get("type", "")
            snapshot_info[f"{volume_type.lower()}_snapshots"].append(
                {"name": volume.get("name"), "uuid": volume.get("uuid"), "installPath": volume.get("installPath")}
            )

        return snapshot_info

    def restore_snapshot(self, snapshot_uuid: str) -> Dict[str, Any]:
        """
        Restore VM from snapshot.

        ZStack 5.x API:
        PUT /zstack/v1/volume-snapshots/{uuid}/actions
        {
            "revertVmFromSnapshot": {}
        }

        :param snapshot_uuid: Snapshot UUID
        :return: Async job result
        """
        payload = {"revertVmFromSnapshot": {}}
        return self._request("PUT", f"/zstack/v1/volume-snapshots/{snapshot_uuid}/actions", json=payload)

    def delete_snapshot(self, snapshot_uuid: str) -> Dict[str, Any]:
        """
        Delete a snapshot.

        ZStack 5.x API:
        DELETE /zstack/v1/volume-snapshots/{uuid}

        :param snapshot_uuid: Snapshot UUID
        :return: Async job result
        """
        return self._request("DELETE", f"/zstack/v1/volume-snapshots/{snapshot_uuid}")

    def query_volume_snapshots(self, volume_uuid: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Query volume snapshots.

        ZStack 5.x API:
        GET /zstack/v1/volume-snapshots
        ?q=volumeUuid=<uuid>  // optional filter

        :param volume_uuid: Optional volume UUID to filter
        :return: List of snapshot information
        """
        params = {}
        if volume_uuid:
            params["q"] = f"volumeUuid={volume_uuid}"

        data = self._request("GET", "/zstack/v1/volume-snapshots", params=params)
        return data.get("inventories", [])

    # ==================== Async Job Operations ====================

    def get_async_job_status(self, job_uuid: str) -> Dict[str, Any]:
        """
        Get asynchronous job status.

        ZStack 5.x API:
        GET /zstack/v1/api-jobs/{uuid}

        :param job_uuid: Job UUID
        :return: Job status information
        """
        return self._request("GET", f"/zstack/v1/api-jobs/{job_uuid}")

    def wait_for_job_completion(self, job_uuid: str, timeout: int = 300, poll_interval: int = 5) -> Dict[str, Any]:
        """
        Wait for an asynchronous job to complete.

        ZStack 5.x async job response:
        {
            "results": [
                {
                    "success": true/false,
                    "error": {...}
                }
            ]
        }

        :param job_uuid: Job UUID
        :param timeout: Maximum wait time in seconds
        :param poll_interval: Polling interval in seconds
        :return: Final job status
        :raise CuckooMachineError: If job fails or times out
        """
        import time

        elapsed = 0

        while elapsed < timeout:
            job_status = self.get_async_job_status(job_uuid)
            results = job_status.get("results", [])

            if not results:
                time.sleep(poll_interval)
                elapsed += poll_interval
                continue

            result = results[0]
            success = result.get("success", False)

            if success is True or success == "True":
                log.debug("Job %s completed successfully", job_uuid)
                return job_status
            elif success is False or success == "False":
                error_info = result.get("error", {})
                raise CuckooMachineError(f"Job {job_uuid} failed: {error_info}")

            time.sleep(poll_interval)
            elapsed += poll_interval

        raise CuckooMachineError(f"Job {job_uuid} timed out after {timeout} seconds")

    # ==================== Query API (ZQL) ====================

    def query_resources(self, resource_type: str, conditions: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Query resources using ZStack Query Language (ZQL).

        ZStack 5.x Query API:
        GET /zstack/v1/<resource-type>
        ?q=<condition1>
        &q=<condition2>

        Example conditions:
        - "name=cuckoo-vm-01"
        - "state=Running"
        - "uuid=<uuid>"

        :param resource_type: Resource type (e.g., vm-instances, volume-snapshots)
        :param conditions: List of query conditions
        :return: List of matching resources
        """
        params = {}
        if conditions:
            params["q"] = conditions

        endpoint = f"/zstack/v1/{resource_type}"
        data = self._request("GET", endpoint, params=params)
        return data.get("inventories", [])
