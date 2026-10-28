import os
import urllib.parse
from typing import Any, Iterator

import httpx
import pytest

BASE_URL = os.getenv("MOCK_GRAPH_BASE_URL", "http://localhost:8088")
DRIVE_ID = os.getenv("MOCK_GRAPH_DRIVE_ID", "kb-docs")
TARGET_GROUP = os.getenv("MOCK_GRAPH_TARGET_GROUP", "eng-backend")

def _is_service_up(base_url: str) -> bool:
    try:
        httpx.get(base_url, timeout=0.5)
        return True
    except httpx.RequestError:
        return False

class MockGraphClient:
    """Helper client encapsulating HTTP calls and URL parsing for Mock Graph Server."""

    def __init__(self, base_url: str = BASE_URL, drive_id: str = DRIVE_ID):
        self.base_url = base_url
        self.drive_id = drive_id
        self._client = httpx.Client(base_url=base_url, timeout=10.0)

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def _auth(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    # --- Admin ---

    def reset(self) -> httpx.Response:
        return self._client.post("/_admin/reset")

    def add_group_member(self, group: str, member: str) -> httpx.Response:
        return self._client.post(f"/_admin/groups/{group}/members", json={"member": member})

    # --- Users & Directory ---

    def list_users(self, token: str = "app", top: int = 5) -> httpx.Response:
        return self._client.get(
            "/v1.0/users",
            headers=self._auth(token),
            params={"$select": "id,displayName,userPrincipalName", "$top": top},
        )

    def get_me(self, token: str) -> httpx.Response:
        return self._client.get("/v1.0/me", headers=self._auth(token))

    def get_my_groups(self, token: str) -> httpx.Response:
        return self._client.get(
            "/v1.0/me/transitiveMemberOf",
            headers=self._auth(token),
            params={"$select": "id,displayName"},
        )

    def get_my_group_names(self, token: str) -> list[str]:
        resp = self.get_my_groups(token)
        assert resp.status_code == 200
        return [g["displayName"] for g in resp.json().get("value", [])]

    # --- Drive Folders & Files ---

    def list_folder_children(
        self, token: str, path: str, select: str = "id,name,folder,file"
    ) -> httpx.Response:
        return self._client.get(
            f"/v1.0/drives/{self.drive_id}/root:{path}:/children",
            headers=self._auth(token),
            params={"$select": select},
        )

    def get_folder_child_names(self, token: str, path: str) -> list[str]:
        resp = self.list_folder_children(token, path)
        assert resp.status_code == 200
        return [item["name"] for item in resp.json().get("value", [])]

    def get_file_metadata(self, token: str, path: str) -> httpx.Response:
        return self._client.get(
            f"/v1.0/drives/{self.drive_id}/root:{path}:",
            headers=self._auth(token),
            params={"$select": "id,name,size,file"},
        )

    def download_file(
        self, token: str, path: str, follow_redirects: bool = True
    ) -> httpx.Response:
        return self._client.get(
            f"/v1.0/drives/{self.drive_id}/root:{path}:/content",
            headers=self._auth(token),
            follow_redirects=follow_redirects,
        )

    # --- Delta Queries ---

    def get_drive_delta(
        self,
        token: str,
        delta_token: str | None = None,
        show_sharing_changes: bool = False,
    ) -> httpx.Response:
        headers = self._auth(token)
        if show_sharing_changes:
            headers["Prefer"] = "deltashowsharingchanges"
        params: dict[str, Any] = (
            {"token": delta_token}
            if delta_token is not None
            else {"$top": 100, "$select": "id,name"}
        )
        return self._client.get(
            f"/v1.0/drives/{self.drive_id}/root/delta",
            headers=headers,
            params=params,
        )

    def get_drive_delta_names(
        self,
        token: str,
        delta_token: str | None = None,
        show_sharing_changes: bool = False,
    ) -> list[str]:
        resp = self.get_drive_delta(
            token=token,
            delta_token=delta_token,
            show_sharing_changes=show_sharing_changes,
        )
        assert resp.status_code == 200
        return [item["name"] for item in resp.json().get("value", []) if "name" in item]

    def get_latest_drive_delta_token(self, token: str) -> str:
        resp = self.get_drive_delta(token=token, delta_token="latest")
        assert resp.status_code == 200
        delta_link = resp.json().get("@odata.deltaLink", "")
        parsed = urllib.parse.parse_qs(urllib.parse.urlparse(delta_link).query)
        extracted = parsed.get("token", [""])[0]
        assert extracted, f"Failed to extract token from deltaLink: {delta_link}"
        return extracted

    def get_groups_delta(
        self, token: str = "app", delta_token: str | None = None
    ) -> httpx.Response:
        params: dict[str, Any] = (
            {"$deltatoken": delta_token}
            if delta_token is not None
            else {"$top": 100, "$select": "id,displayName,members"}
        )
        return self._client.get(
            "/v1.0/groups/delta",
            headers=self._auth(token),
            params=params,
        )

    def get_latest_groups_delta_token(self, token: str = "app") -> str:
        resp = self.get_groups_delta(token=token, delta_token="latest")
        assert resp.status_code == 200
        delta_link = resp.json().get("@odata.deltaLink", "")
        parsed = urllib.parse.parse_qs(urllib.parse.urlparse(delta_link).query)
        extracted = parsed.get("$deltatoken", [""])[0]
        assert extracted, f"Failed to extract $deltatoken from deltaLink: {delta_link}"
        return extracted


@pytest.fixture
def graph() -> Iterator[MockGraphClient]:
    """
    Step 0.1 & Step 6.1:
    Provides a MockGraphClient and resets server state before and after each test.
    """
    client = MockGraphClient()
    setup_resp = client.reset()
    assert setup_resp.status_code == 200, f"Setup reset failed: {setup_resp.text}"
    try:
        yield client
    finally:
        teardown_resp = client.reset()
        assert teardown_resp.status_code == 200, f"Teardown reset failed: {teardown_resp.text}"
        client.close()

@pytest.mark.skipif(
    not _is_service_up(BASE_URL),
    reason=f"Service is not reachable at {BASE_URL}",
)
class TestMockGraphAuthAndDirectory:
    """Step 1: Authentication Contexts & User Directory Queries."""

    def test_1_1_list_users_app_token(self, graph: MockGraphClient):
        response = graph.list_users(token="app", top=5)
        assert response.status_code == 200
        data = response.json()
        assert "@odata.context" in data
        assert len(data["value"]) <= 5
        for user in data["value"]:
            assert {"id", "displayName", "userPrincipalName"}.issubset(user.keys())

    def test_1_2_app_token_on_me_rejected(self, graph: MockGraphClient):
        response = graph.get_me(token="app")
        assert response.status_code == 400

    def test_1_3_user_alice_profile(self, graph: MockGraphClient):
        response = graph.get_me(token="user:alice")
        assert response.status_code == 200
        assert response.json().get("displayName") == "Alice Backend"

    def test_1_4_alice_transitive_member_of(self, graph: MockGraphClient):
        group_names = graph.get_my_group_names(token="user:alice")
        assert {"Engineering - Backend", "Engineering", "All Staff"}.issubset(group_names)

    def test_1_5_user_bob_profile(self, graph: MockGraphClient):
        response = graph.get_me(token="user:bob")
        assert response.status_code == 200
        assert response.json().get("displayName") == "Bob Frontend"

    def test_1_6_bob_transitive_member_of(self, graph: MockGraphClient):
        group_names = graph.get_my_group_names(token="user:bob")
        assert {"Engineering - Frontend", "Engineering"}.issubset(group_names)
        assert "Engineering - Backend" not in group_names

    def test_1_7_invalid_auth_token_rejected(self, graph: MockGraphClient):
        response = graph.list_users(token="user:unknown-principal")
        assert response.status_code == 401

@pytest.mark.skipif(
    not _is_service_up(BASE_URL),
    reason=f"Service is not reachable at {BASE_URL}",
)
class TestMockGraphRbacAndFiles:
    """Steps 2 & 3: Folder RBAC & File Metadata/Content Access."""

    def test_2_1_approach_a_alice_children_engineering(self, graph: MockGraphClient):
        names = graph.get_folder_child_names(token="user:alice", path="/Engineering")
        assert "Backend" in names

    def test_2_2_approach_a_bob_children_engineering_security_trimmed(self, graph: MockGraphClient):
        names = graph.get_folder_child_names(token="user:bob", path="/Engineering")
        assert "Backend" not in names
        assert "local-dev-setup.md" in names

    def test_2_3_approach_b_alice_direct_backend(self, graph: MockGraphClient):
        names = graph.get_folder_child_names(token="user:alice", path="/Engineering/Backend")
        assert "db-migrations.md" in names

    def test_2_4_approach_b_bob_direct_backend_not_found(self, graph: MockGraphClient):
        response = graph.list_folder_children(token="user:bob", path="/Engineering/Backend")
        assert response.status_code == 404

    def test_3_1_alice_file_metadata(self, graph: MockGraphClient):
        response = graph.get_file_metadata(
            token="user:alice", path="/Engineering/Backend/db-migrations.md"
        )
        assert response.status_code == 200
        assert response.json().get("name") == "db-migrations.md"

    def test_3_2_alice_download_file_content(self, graph: MockGraphClient):
        file_path = "/Engineering/Backend/db-migrations.md"

        redirect_resp = graph.download_file(
            token="user:alice", path=file_path, follow_redirects=False
        )
        assert redirect_resp.status_code == 302
        assert "_download" in redirect_resp.headers.get("location", "")

        content_resp = graph.download_file(
            token="user:alice", path=file_path, follow_redirects=True
        )
        assert content_resp.status_code == 200
        assert len(content_resp.text) > 0

    def test_3_3_bob_file_metadata_not_found(self, graph: MockGraphClient):
        response = graph.get_file_metadata(
            token="user:bob", path="/Engineering/Backend/db-migrations.md"
        )
        assert response.status_code == 404

    def test_3_4_bob_download_file_content_not_found(self, graph: MockGraphClient):
        response = graph.download_file(
            token="user:bob", path="/Engineering/Backend/db-migrations.md"
        )
        assert response.status_code == 404

@pytest.mark.skipif(
    not _is_service_up(BASE_URL),
    reason=f"Service is not reachable at {BASE_URL}",
)
def test_e2e_dynamic_group_access_and_delta_sync(graph: MockGraphClient):
    """
    Steps 4 & 5 Full E2E Flow:
    1. Bob's initial drive delta omits restricted items (4.1).
    2. Capture latest delta tokens for Bob's drive (4.2) and Groups (4.3).
    3. Admin adds Bob to 'eng-backend' (4.4).
    4. Full deltas now include Backend items (5.1) and group member diff (5.2).
    5. Incremental deltas since captured tokens return only new changes (5.3, 5.4).
    6. Bob now has group membership (5.5), folder access (5.6, 5.7), and file download (5.8).
    """
    # 4.1 Before change: restricted items are omitted from Bob's drive delta
    initial_items = graph.get_drive_delta_names(token="user:bob")
    assert "Backend" not in initial_items
    assert "db-migrations.md" not in initial_items

    # 4.2 & 4.3 Capture incremental delta tokens before admin action
    bob_drive_token = graph.get_latest_drive_delta_token(token="user:bob")
    groups_token = graph.get_latest_groups_delta_token()

    # 4.4 Admin adds Bob to 'eng-backend'
    admin_resp = graph.add_group_member(TARGET_GROUP, "user:bob")
    assert admin_resp.status_code == 200
    assert admin_resp.json().get("changed") is True

    # 5.1 & 5.2 Full deltas after change include restricted items and group members@delta
    updated_items = graph.get_drive_delta_names(token="user:bob")
    assert {"Backend", "db-migrations.md"}.issubset(updated_items)

    full_groups = graph.get_groups_delta().json().get("value", [])
    eng_backend = next(g for g in full_groups if g.get("displayName") == "Engineering - Backend")
    assert "members@delta" in eng_backend

    # 5.3 & 5.4 Incremental deltas using captured tokens return only the new changes
    incremental_items = graph.get_drive_delta_names(
        token="user:bob", delta_token=bob_drive_token, show_sharing_changes=True
    )
    assert {"Backend", "db-migrations.md"}.issubset(incremental_items)

    inc_groups = graph.get_groups_delta(delta_token=groups_token).json().get("value", [])
    inc_eng_backend = next(g for g in inc_groups if g.get("displayName") == "Engineering - Backend")
    assert len(inc_eng_backend.get("members@delta", [])) > 0

    # 5.5 - 5.8 Bob now has group membership, folder visibility, and file download access
    assert "Engineering - Backend" in graph.get_my_group_names(token="user:bob")
    assert "Backend" in graph.get_folder_child_names(token="user:bob", path="/Engineering")
    assert "db-migrations.md" in graph.get_folder_child_names(
        token="user:bob", path="/Engineering/Backend"
    )

    download_resp = graph.download_file(
        token="user:bob", path="/Engineering/Backend/db-migrations.md"
    )
    assert download_resp.status_code == 200
    assert len(download_resp.text) > 0
