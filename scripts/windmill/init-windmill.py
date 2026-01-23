#!/usr/bin/env python3
"""Initialize Windmill workspace and create API token.

This script:
1. Waits for Windmill server to be ready
2. Logs in with default superadmin credentials
3. Creates the 'goat' workspace if it doesn't exist
4. Creates an API token for the goat workspace
5. Outputs the token for use in .env

Environment variables:
    WINDMILL_URL: Windmill server URL (default: http://windmill-server:8000)
    WINDMILL_WORKSPACE: Workspace to create (default: goat)
    WINDMILL_ADMIN_EMAIL: Admin email (default: admin@windmill.dev)
    WINDMILL_ADMIN_PASSWORD: Admin password (default: changeme)
    WINDMILL_TOKEN_FILE: File to write the token to (optional)
"""

import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def wait_for_windmill(base_url: str, max_retries: int = 30, delay: int = 2) -> bool:
    """Wait for Windmill server to be ready."""
    print(f"Waiting for Windmill server at {base_url}...")

    for attempt in range(max_retries):
        try:
            req = Request(f"{base_url}/api/version")
            with urlopen(req, timeout=5) as response:
                if response.status == 200:
                    version = response.read().decode().strip().strip('"')
                    print(f"Windmill server is ready (version: {version})")
                    return True
        except (HTTPError, URLError, TimeoutError) as e:
            if attempt < max_retries - 1:
                print(f"  Attempt {attempt + 1}/{max_retries}: Not ready yet ({e})")
                time.sleep(delay)
            else:
                print(f"ERROR: Windmill server not ready after {max_retries} attempts")
                return False
    return False


def api_request(
    base_url: str,
    endpoint: str,
    method: str = "GET",
    data: dict = None,
    token: str = None,
    expect_json: bool = True,
) -> dict | str:
    """Make an API request to Windmill."""
    url = f"{base_url}/api{endpoint}"
    headers = {"Content-Type": "application/json"}

    if token:
        headers["Authorization"] = f"Bearer {token}"

    body = json.dumps(data).encode() if data else None
    req = Request(url, data=body, headers=headers, method=method)

    try:
        with urlopen(req, timeout=30) as response:
            content = response.read().decode()
            if not content:
                return {} if expect_json else ""
            if expect_json:
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    # Some endpoints return plain strings
                    return content.strip()
            return content.strip()
    except HTTPError as e:
        error_body = e.read().decode() if e.fp else ""
        raise Exception(f"API error {e.code} on {endpoint}: {error_body}") from e


def login(base_url: str, email: str, password: str) -> str:
    """Login to Windmill and return the session token."""
    print(f"Logging in as {email}...")

    result = api_request(
        base_url,
        "/auth/login",
        method="POST",
        data={"email": email, "password": password},
        expect_json=False,
    )

    # Windmill returns the token as a plain string
    token = result if isinstance(result, str) else result.get("token")
    if not token:
        raise Exception(f"Login failed: no token in response: {result}")

    print("  Login successful")
    return token


def workspace_exists(base_url: str, token: str, workspace_id: str) -> bool:
    """Check if a workspace exists."""
    try:
        result = api_request(
            base_url,
            "/workspaces/exists",
            method="POST",
            data={"id": workspace_id},
            token=token,
        )
        return result.get("exists", False)
    except Exception:
        return False


def create_workspace(base_url: str, token: str, workspace_id: str, name: str) -> None:
    """Create a new workspace."""
    print(f"Creating workspace '{workspace_id}'...")

    api_request(
        base_url,
        "/workspaces/create",
        method="POST",
        data={
            "id": workspace_id,
            "name": name,
        },
        token=token,
    )

    print(f"  Workspace '{workspace_id}' created")


def create_api_token(base_url: str, token: str, label: str = "goat-api") -> str:
    """Create an API token."""
    print(f"Creating API token '{label}'...")

    result = api_request(
        base_url,
        "/users/tokens/create",
        method="POST",
        data={
            "label": label,
            "expiration": None,  # No expiration
        },
        token=token,
        expect_json=False,
    )

    # Windmill may return the token as a plain string
    api_token = result if isinstance(result, str) else result.get("token")
    if not api_token:
        raise Exception(f"Failed to create token: {result}")

    print("  API token created successfully")
    return api_token


def create_folder(
    base_url: str, token: str, workspace_id: str, folder_name: str
) -> None:
    """Create a folder in the workspace."""
    try:
        api_request(
            base_url,
            f"/w/{workspace_id}/folders/create",
            method="POST",
            data={"name": folder_name},
            token=token,
        )
        print(f"  Created folder: {folder_name}")
    except Exception as e:
        error_str = str(e).lower()
        if "already exists" in error_str or "duplicate" in error_str:
            print(f"  Folder already exists: {folder_name}")
        else:
            print(f"  Warning creating folder {folder_name}: {e}")


def verify_script_creation(
    base_url: str, token: str, workspace_id: str, max_retries: int = 10, delay: int = 2
) -> bool:
    """Verify that Windmill can create scripts by creating and deleting a test script.

    This ensures Windmill is fully initialized before the sync script runs.
    """
    test_path = "f/goat/tools/_init_test"
    test_content = "def main(): return 'test'"

    print("Verifying Windmill is ready for script creation...")

    for attempt in range(max_retries):
        try:
            # Try to create a test script
            api_request(
                base_url,
                f"/w/{workspace_id}/scripts/create",
                method="POST",
                data={
                    "path": test_path,
                    "content": test_content,
                    "summary": "Init test",
                    "description": "Temporary test script",
                    "language": "python3",
                },
                token=token,
                expect_json=False,
            )

            # Delete the test script
            try:
                api_request(
                    base_url,
                    f"/w/{workspace_id}/scripts/delete/p/{test_path}",
                    method="POST",
                    token=token,
                    expect_json=False,
                )
            except Exception:
                pass  # Ignore delete errors

            print("  Windmill is ready for script creation")
            return True

        except Exception as e:
            if attempt < max_retries - 1:
                print(f"  Attempt {attempt + 1}/{max_retries}: Not ready yet ({e})")
                time.sleep(delay)
            else:
                print(
                    f"ERROR: Windmill not ready for script creation after {max_retries} attempts"
                )
                return False

    return False


def main() -> int:
    """Initialize Windmill workspace and create API token."""
    # Get configuration from environment
    base_url = os.environ.get("WINDMILL_URL", "http://windmill-server:8000").rstrip("/")
    workspace_id = os.environ.get("WINDMILL_WORKSPACE", "goat")
    admin_email = os.environ.get("WINDMILL_ADMIN_EMAIL", "admin@windmill.dev")
    admin_password = os.environ.get("WINDMILL_ADMIN_PASSWORD", "changeme")
    token_file = os.environ.get("WINDMILL_TOKEN_FILE")

    print("=" * 60)
    print("Windmill Initialization")
    print("=" * 60)
    print(f"  URL: {base_url}")
    print(f"  Workspace: {workspace_id}")
    print(f"  Admin: {admin_email}")
    print()

    # Check if token file already exists and is valid
    if token_file and os.path.exists(token_file):
        with open(token_file, "r") as f:
            existing_token = f.read().strip()
        if existing_token:
            print(f"Token file already exists: {token_file}")
            print("Skipping initialization (delete token file to re-initialize)")
            print()
            print("=" * 60)
            print("Windmill already initialized!")
            print("=" * 60)
            return 0

    # Wait for Windmill to be ready
    if not wait_for_windmill(base_url):
        return 1

    try:
        # Login with default credentials
        session_token = login(base_url, admin_email, admin_password)

        # Check if workspace exists
        if workspace_exists(base_url, session_token, workspace_id):
            print(f"Workspace '{workspace_id}' already exists")
        else:
            create_workspace(
                base_url, session_token, workspace_id, workspace_id.title()
            )

        # Create required folders for tools
        # Script path f/goat/tools/buffer means folder "goat" containing folder "tools"
        # We need to create "goat" first, then "goat/tools"
        print("Creating folders for tools...")
        create_folder(base_url, session_token, workspace_id, "goat")

        # Create API token
        api_token = create_api_token(base_url, session_token, f"{workspace_id}-api")

        # Verify Windmill is ready for script creation before completing
        # This prevents the sync script from running too early
        if not verify_script_creation(base_url, api_token, workspace_id):
            return 1

        # Output the token
        print()
        print("=" * 60)
        print("Windmill initialization complete!")
        print("=" * 60)
        print()
        print("Add the following to your .env file:")
        print()
        print(f"  WINDMILL_TOKEN={api_token}")
        print()

        # Write token to file if requested
        if token_file:
            os.makedirs(os.path.dirname(token_file) or ".", exist_ok=True)
            with open(token_file, "w") as f:
                f.write(api_token)
            print(f"Token written to: {token_file}")

        # Also print to stdout for easy capture
        print(f"WINDMILL_TOKEN={api_token}")

        return 0

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
