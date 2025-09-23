"""
Google Docs tools for LangChain agents.

Features (OAuth user flow, no service account):
- docs_create: create a new Google Doc with a title
- docs_get: fetch document metadata and a brief content summary
- docs_append: append plain text to the end (or at an index) of a doc
- docs_export_pdf: export a Google Doc to PDF and save under ./exports/

Scopes (override with GDOCS_SCOPES):
- https://www.googleapis.com/auth/documents
- https://www.googleapis.com/auth/drive.file   (needed for export)

Env (optional):
- GDOCS_CLIENT_SECRETS_PATH or GDOCS_CREDENTIALS_PATH (folder or file)
- GDOCS_TOKEN_PATH (defaults to docs_token.json next to credentials)
- GOOGLE_OAUTH_REDIRECT_URI or GDOCS_REDIRECT_URI for building auth URL
"""

from __future__ import annotations

import os
import json
import logging
from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Type

try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request, AuthorizedSession
    from googleapiclient.discovery import build
except Exception:  # pragma: no cover - optional dependency
    Credentials = None  # type: ignore
    Request = None  # type: ignore
    AuthorizedSession = None  # type: ignore
    build = None  # type: ignore

from utils.google_oauth import ensure_agent_token_file

try:
    from pydantic import BaseModel, Field
except Exception:  # pragma: no cover
    from pydantic.v1 import BaseModel, Field  # type: ignore

try:
    from langchain.tools import BaseTool
except Exception:  # pragma: no cover
    from langchain.agents import Tool as BaseTool  # type: ignore


logger = logging.getLogger(__name__)

SCOPES = [
    s.strip()
    for s in os.getenv(
        "GDOCS_SCOPES",
        "https://www.googleapis.com/auth/documents,https://www.googleapis.com/auth/drive.file",
    ).split(",")
    if s.strip()
]


def _default_docs_dir() -> str:
    base_dir = os.getenv("GDOCS_CREDENTIALS_DIR") or os.getenv("CREDENTIALS_DIR")
    if base_dir:
        if base_dir == os.getenv("CREDENTIALS_DIR"):
            return os.path.join(base_dir, "docs")
        return base_dir
    return os.path.join(os.getcwd(), ".credentials", "docs")


@dataclass
class DocsConfig:
    credentials_file: str = "credentials.json"
    token_file: str = ""
    agent_id: Optional[str] = None


class GoogleDocsClient:
    def __init__(self, config: Optional[DocsConfig] = None):
        self.config = config or DocsConfig()
        if os.path.isdir(self.config.credentials_file):
            self.config.credentials_file = os.path.join(
                self.config.credentials_file, "credentials.json"
            )
        if not self.config.token_file:
            base_dir = os.path.dirname(self.config.credentials_file) if self.config.credentials_file else _default_docs_dir()
            if not base_dir:
                base_dir = _default_docs_dir()
            self.config.token_file = os.path.join(base_dir, "docs_token.json")
        elif os.path.isdir(self.config.token_file):
            self.config.token_file = os.path.join(self.config.token_file, "docs_token.json")
        self.docs_service = None
        self.drive_service = None
        self.session: Optional[AuthorizedSession] = None
        self._init_error: Optional[str] = None
        try:
            self._init_services()
        except Exception as exc:
            # Defer errors until tool execution so agents can still load without credentials.
            self._init_error = str(exc)
            logger.info("Google Docs client unavailable until OAuth completes: %s", exc)

    def _ensure_ready(self) -> None:
        """Raise a clear error when the client is not ready to serve requests."""

        if self._init_error:
            raise RuntimeError(
                "Google Docs tool unavailable: " + self._init_error
            )
        if not (self.docs_service or self.session):
            raise RuntimeError("Google Docs client not initialized")

    def _get_credentials(self) -> Credentials:
        if Credentials is None or Request is None:
            raise RuntimeError("Google client libraries not installed for Docs operations.")
        creds = None
        fallback_token = self.config.token_file or os.path.join(_default_docs_dir(), "docs_token.json")
        token_path, _ = ensure_agent_token_file(self.config.agent_id, fallback_token, filename="token.json")
        token_file = token_path or fallback_token
        self.config.token_file = token_file

        if token_file and os.path.exists(token_file):
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)
            # Validate scopes
            have = set(creds.scopes or [])
            need = set(SCOPES)
            if need and not need.issubset(have):
                raise RuntimeError(
                    f"Docs token missing scopes; have={sorted(have)}, need={sorted(need)}. Re-authorize."
                )
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                who = f" for agent {self.config.agent_id}" if self.config.agent_id else ""
                raise RuntimeError(
                    "Google Docs OAuth token not found or invalid"
                    + who
                    + f" at {token_file}. Please authorize via the Docs OAuth URL."
                )
        return creds

    def _init_services(self):
        creds = self._get_credentials()
        fallback_reason: Optional[str] = None
        if build is not None:
            try:
                self.docs_service = build("docs", "v1", credentials=creds, cache_discovery=False)
                self.drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)
            except Exception as exc:
                fallback_reason = str(exc)
                self.docs_service = None
                self.drive_service = None
        else:
            fallback_reason = "googleapiclient.discovery not available"

        if self.docs_service and self.drive_service:
            return

        if AuthorizedSession is None:
            raise RuntimeError(
                "Google Docs discovery client unavailable and REST session cannot be created without google-auth libraries."
            )

        if fallback_reason:
            logger.info(
                "Google Docs discovery client unavailable (%s); using REST session", fallback_reason
            )
        self.session = AuthorizedSession(creds)

    # ---------- Docs ops (REST fallbacks included) ----------
    def docs_get(self, document_id: str) -> Dict[str, Any]:
        self._ensure_ready()
        if self.docs_service:
            return self.docs_service.documents().get(documentId=document_id).execute()
        r = self.session.get(f"https://docs.googleapis.com/v1/documents/{document_id}", timeout=20)
        r.raise_for_status(); return r.json()

    def docs_create(self, title: str) -> Dict[str, Any]:
        self._ensure_ready()
        body = {"title": title}
        if self.docs_service:
            return self.docs_service.documents().create(body=body).execute()
        r = self.session.post("https://docs.googleapis.com/v1/documents", json=body, timeout=20)
        r.raise_for_status(); return r.json()

    def docs_batch_update(self, document_id: str, requests: List[Dict[str, Any]]) -> Dict[str, Any]:
        self._ensure_ready()
        body = {"requests": requests}
        if self.docs_service:
            return (
                self.docs_service.documents()
                .batchUpdate(documentId=document_id, body=body)
                .execute()
            )
        r = self.session.post(
            f"https://docs.googleapis.com/v1/documents/{document_id}:batchUpdate",
            json=body,
            timeout=20,
        )
        r.raise_for_status(); return r.json()

    def drive_export_pdf(self, file_id: str) -> bytes:
        self._ensure_ready()
        if self.drive_service:
            request = self.drive_service.files().export_media(fileId=file_id, mimeType="application/pdf")
            import io as _io
            from googleapiclient.http import MediaIoBaseDownload as _MID
            buf = _io.BytesIO()
            downloader = _MID(buf, request)
            done = False
            while not done:
                _status, done = downloader.next_chunk()
            return buf.getvalue()
        r = self.session.get(
            f"https://www.googleapis.com/drive/v3/files/{file_id}/export",
            params={"mimeType": "application/pdf"},
            timeout=30,
        )
        r.raise_for_status(); return r.content


# ---------- Pydantic Args ----------
class DocsCreateArgs(BaseModel):
    title: str = Field(description="Title for the new document")


class DocsGetArgs(BaseModel):
    document_id: str = Field(description="Google Docs documentId")


class DocsAppendArgs(BaseModel):
    document_id: str = Field(description="Google Docs documentId")
    text: str = Field(description="Text to append")
    location_index: Optional[int] = Field(default=None, description="Insert index; defaults to end of document")
    new_page: bool = Field(default=False, description="Insert a page break before appending the text")


class DocsExportArgs(BaseModel):
    document_id: str = Field(description="Google Docs documentId")
    format: str = Field(default="pdf", description="Only 'pdf' is supported")


class DocsUnifiedArgs(BaseModel):
    action: str = Field(description="create|get|append|export")
    title: Optional[str] = None
    document_id: Optional[str] = None
    text: Optional[str] = None
    location_index: Optional[int] = None
    format: Optional[str] = "pdf"
    new_page: Optional[bool] = False


# ---------- Tools ----------
class DocsCreateTool(BaseTool):
    name: str = "docs_create"
    description: str = "Create a new Google Document with the given title"
    args_schema: Type[BaseModel] = DocsCreateArgs
    client: GoogleDocsClient

    def _run(self, title: str) -> str:
        try:
            doc = self.client.docs_create(title)
            return json.dumps({"documentId": doc.get("documentId"), "title": doc.get("title")})
        except Exception as e:
            return f"Docs create failed: {e}"


class DocsGetTool(BaseTool):
    name: str = "docs_get"
    description: str = "Get a Google Doc's title and a brief summary of its content"
    args_schema: Type[BaseModel] = DocsGetArgs
    client: GoogleDocsClient

    def _run(self, document_id: str) -> str:
        try:
            d = self.client.docs_get(document_id)
            title = d.get("title")
            content = d.get("body", {}).get("content", [])
            # Extract first few text runs
            texts: List[str] = []
            for elem in content:
                p = elem.get("paragraph")
                if not p:
                    continue
                for e in p.get("elements", []):
                    t = e.get("textRun", {}).get("content")
                    if t:
                        texts.append(t)
                if len("".join(texts)) > 500:
                    break
            snippet = ("".join(texts)).strip()
            if len(snippet) > 500:
                snippet = snippet[:500] + "..."
            return json.dumps({"title": title, "snippet": snippet})
        except Exception as e:
            return f"Docs get failed: {e}"


class DocsAppendTool(BaseTool):
    name: str = "docs_append"
    description: str = "Append/inject plain text into a Google Doc"
    args_schema: Type[BaseModel] = DocsAppendArgs
    client: GoogleDocsClient

    def _run(self, document_id: str, text: str, location_index: Optional[int] = None, new_page: bool = False) -> str:
        try:
            requests: List[Dict[str, Any]] = []
            if new_page:
                # Compute an insertion index at end of document for page break
                d = self.client.docs_get(document_id)
                body = d.get("body", {})
                content = body.get("content", [])
                if not content:
                    end_idx = 1
                else:
                    end_idx = int((content[-1].get("endIndex") or 1)) - 1
                    if end_idx < 1:
                        end_idx = 1
                requests.append({"insertPageBreak": {"location": {"index": end_idx}}})
                requests.append({"insertText": {"location": {"index": end_idx + 1}, "text": text}})
            else:
                if location_index is not None:
                    requests.append({"insertText": {"location": {"index": int(location_index)}, "text": text}})
                else:
                    requests.append({"insertText": {"endOfSegmentLocation": {}, "text": text}})
            self.client.docs_batch_update(document_id, requests)
            return "Text appended to document successfully"
        except Exception as e:
            return f"Docs append failed: {e}"


class DocsExportPDFTool(BaseTool):
    name: str = "docs_export_pdf"
    description: str = "Export a Google Doc to PDF and save under ./exports/{document_id}.pdf"
    args_schema: Type[BaseModel] = DocsExportArgs
    client: GoogleDocsClient

    def _run(self, document_id: str, format: str = "pdf") -> str:
        try:
            fmt = (format or "pdf").lower().strip()
            if fmt != "pdf":
                return "Only PDF export is supported"
            data = self.client.drive_export_pdf(document_id)
            out_dir = os.path.join(os.getcwd(), "exports")
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, f"{document_id}.pdf")
            with open(out_path, "wb") as f:
                f.write(data)
            return f"Exported to {out_path}"
        except Exception as e:
            return f"Docs export failed: {e}"


class DocsUnifiedTool(BaseTool):
    name: str = "google_docs"
    description: str = (
        "Google Docs unified tool. Actions: create|get|append|export. "
        "Use create(title), get(document_id), append(document_id,text,location_index?), export(document_id)."
    )
    args_schema: Type[BaseModel] = DocsUnifiedArgs
    client: GoogleDocsClient

    def _run(
        self,
        action: str,
        title: Optional[str] = None,
        document_id: Optional[str] = None,
        text: Optional[str] = None,
        location_index: Optional[int] = None,
        format: Optional[str] = "pdf",
        new_page: Optional[bool] = False,
    ) -> str:
        a = (action or "").strip().lower()
        if a == "create":
            if not title:
                return "Docs create failed: missing title"
            return DocsCreateTool(client=self.client)._run(title=title)
        if a == "get":
            if not document_id:
                return "Docs get failed: missing document_id"
            return DocsGetTool(client=self.client)._run(document_id=document_id)
        if a == "append":
            if not (document_id and text):
                return "Docs append failed: missing document_id/text"
            return DocsAppendTool(client=self.client)._run(document_id=document_id, text=text, location_index=location_index, new_page=bool(new_page))
        if a == "export":
            if not document_id:
                return "Docs export failed: missing document_id"
            return DocsExportPDFTool(client=self.client)._run(document_id=document_id, format=format or "pdf")
        return "Docs tool failed: unknown action (use create|get|append|export)"


def initialize_docs_tools(
    credentials_file: str,
    token_file: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> List[BaseTool]:
    cfg = DocsConfig(credentials_file=credentials_file, token_file=(token_file or ""), agent_id=agent_id)
    client = GoogleDocsClient(cfg)
    tools: List[BaseTool] = [
        DocsUnifiedTool(client=client),
        DocsCreateTool(client=client),
        DocsGetTool(client=client),
        DocsAppendTool(client=client),
        DocsExportPDFTool(client=client),
    ]
    return tools


def build_docs_oauth_url(state: Optional[str] = None) -> Optional[str]:
    # Resolve secrets
    cands = [
        os.getenv("GDOCS_CLIENT_SECRETS_PATH"),
        (os.path.join(os.getenv("GDOCS_CREDENTIALS_PATH", ""), "credentials.json")
         if os.getenv("GDOCS_CREDENTIALS_PATH") and os.path.isdir(os.getenv("GDOCS_CREDENTIALS_PATH", ""))
         else os.getenv("GDOCS_CREDENTIALS_PATH")),
        os.path.join(os.getcwd(), "credential_folder", "credentials.json"),
        os.path.join(os.getcwd(), "credentials.json"),
        os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
    ]
    secrets_path = next((p for p in cands if p and os.path.exists(p)), None)
    redirect_uri = (
        os.getenv("GOOGLE_OAUTH_REDIRECT_URI")
        or os.getenv("OAUTH_REDIRECT_URI")
        or os.getenv("GDOCS_REDIRECT_URI")
    )
    if not secrets_path or not redirect_uri:
        return None
    try:
        with open(secrets_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        client_id = (data.get("web", {}) or {}).get("client_id") or (data.get("installed", {}) or {}).get("client_id")
        if not client_id:
            return None
    except Exception:
        return None
    from urllib.parse import urlencode
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    if state:
        params["state"] = state
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)


__all__ = [
    "initialize_docs_tools",
    "build_docs_oauth_url",
]
