"""File Intelligence Layer — upload once, reference everywhere.

General-purpose file upload infrastructure for NovoMCP. Decouples large
files from conversation context and enables cross-surface state continuity.

Architecture:
    1. Client calls generate_upload_url → S3 presigned PUT URL + file_id
    2. Client PUTs file directly to S3 (no MCP gateway bottleneck)
    3. Upload confirmation updates Aurora files.mcp_files record
    4. Downstream tools reference file_id (not inline content)
    5. File record tracks provenance: linked_tool_calls, linked_job_ids,
       parent_file_id (self-FK in Aurora)

AWS resources:
    - S3:     novomcp-files / uploads/{org_id}/{file_id}/{filename}
    - Aurora: files.mcp_files schema (PK file_id; provenance via parent_file_id)

The constructor accepts several legacy positional arguments that are now
ignored (auth is via boto3 + Aurora pool). They stay in the signature so
existing call sites don't need to change.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras
import psycopg2.pool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

S3_BUCKET = os.getenv("FILE_INTELLIGENCE_BUCKET", "")
S3_PREFIX = os.getenv("FILE_INTELLIGENCE_PREFIX", "uploads")  # so blob keys
                                                              # are uploads/{org}/{file_id}/{name}
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

# Default TTLs (unchanged)
UPLOAD_URL_TTL_MINUTES = 30
FILE_TTL_DAYS_FREE = 7
FILE_TTL_DAYS_PAID = None

# Content-hash cap: skip server-side SHA-256 above this size to avoid pinning
# a worker on a multi-GB upload. Files above the cap are still confirmed as
# uploaded; only (org_id, content_hash) dedup is bypassed for them.
CONTENT_HASH_MAX_SIZE = int(os.getenv("CONTENT_HASH_MAX_SIZE", str(500 * 1024 * 1024)))  # 500 MB
_HASH_CHUNK_SIZE = 1024 * 1024  # 1 MB per read; balances mem + S3 chunk overhead

ALLOWED_FILE_TYPES = {
    "qm_log":     {".log", ".out", ".fchk"},
    "pdb":        {".pdb", ".cif", ".mmcif"},
    "trajectory": {".xtc", ".trr", ".dcd", ".nc"},
    "library":    {".sdf", ".smi", ".csv", ".tsv"},
    "frcmod":     {".frcmod", ".prep", ".top", ".gro", ".itp"},
    "custom":     set(),
}

MAX_FILE_SIZE_BYTES = {
    "qm_log":     50 * 1024 * 1024,
    "pdb":        50 * 1024 * 1024,
    "trajectory": 2 * 1024 * 1024 * 1024,
    "library":    200 * 1024 * 1024,
    "frcmod":     10 * 1024 * 1024,
    "custom":     100 * 1024 * 1024,
}


# ---------------------------------------------------------------------------
# Aurora helpers — shared module-level so connection pool is one-per-pod
# ---------------------------------------------------------------------------

_pg_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None
_pg_password_cache: Optional[str] = None


def _get_aurora_password() -> Optional[str]:
    global _pg_password_cache
    if _pg_password_cache is not None:
        return _pg_password_cache
    db_pw = os.getenv("DB_PASSWORD", "")
    if db_pw:
        _pg_password_cache = db_pw
        return db_pw
    try:
        import boto3
        sm = boto3.client("secretsmanager", region_name=AWS_REGION)
        raw = sm.get_secret_value(SecretId=os.getenv("AURORA_SECRET_ID", "novomcp/aurora-admin-password"))["SecretString"]
        try:
            d = json.loads(raw)
            pw = d.get("password") or d.get("Password") or next(iter(d.values()))
        except json.JSONDecodeError:
            pw = raw.strip()
        _pg_password_cache = pw
        return pw
    except Exception as e:
        logger.warning(f"File Intelligence: Aurora password fetch failed: {e}")
        return None


def _get_pool() -> Optional[psycopg2.pool.ThreadedConnectionPool]:
    global _pg_pool
    if _pg_pool is not None:
        return _pg_pool
    # Skip pool init when Aurora isn't configured — file intelligence
    # simply operates without a persistence layer in that case.
    if not os.getenv("AURORA_HOST"):
        return None
    pw = _get_aurora_password()
    if not pw:
        return None
    try:
        _pg_pool = psycopg2.pool.ThreadedConnectionPool(
            1, 5,
            host=os.getenv("AURORA_HOST", ""),
            port=int(os.getenv("AURORA_PORT", "5432")),
            dbname=os.getenv("AURORA_DB", "postgres"),
            user=os.getenv("AURORA_USER", "postgres"),
            password=pw,
            application_name="novomcp-files",
            connect_timeout=10,
            sslmode="require",
            options="-c search_path=files,public",
        )
    except Exception as e:
        logger.debug(f"File Intelligence: Aurora pool init failed: {e}")
        return None
    return _pg_pool


def _pg_conn():
    pool = _get_pool()
    if pool is None:
        raise RuntimeError("Aurora pool unavailable for files schema")
    return pool


# ---------------------------------------------------------------------------
# File Intelligence Client
# ---------------------------------------------------------------------------

class FileIntelligenceClient:
    """Manages file uploads, records, and provenance for NovoMCP.

    Uses S3 (novomcp-files/uploads/) for file content and Aurora
    (files.mcp_files) for metadata + provenance. Same constructor surface
    as the legacy Azure version so call sites don't change.
    """

    def __init__(
        self,
        storage_connection_string: str = "",
        cosmos_endpoint: str = "",
        cosmos_key: str = "",
    ):
        # Args ignored — IRSA + boto3 + Aurora pool handle auth.
        # Kept for backward-compatible call sites in mcp/tools.py.
        import boto3
        from botocore.config import Config
        # SigV4 is required because novomcp-files is SSE-KMS encrypted; S3
        # rejects SigV2 presigned URLs against KMS objects with 403
        # SignatureDoesNotMatch. SigV4 also avoids baking Content-Type into
        # the signature, so clients can PUT with any (or no) Content-Type
        # header without invalidating the URL. Distinct from the
        # kms:GenerateDataKey IAM gotcha (that's an IAM permission issue);
        # this is the signature algorithm itself.
        self._s3 = boto3.client(
            "s3",
            region_name=AWS_REGION,
            config=Config(signature_version="s3v4"),
        )
        self._bucket = S3_BUCKET
        self._prefix = S3_PREFIX
        # Touch the pool eagerly so init failures surface at startup, not
        # on the first request.
        try:
            _get_pool()
        except Exception:
            pass
        logger.info(
            f"FileIntelligenceClient initialized: "
            f"s3=s3://{self._bucket}/{self._prefix}/, "
            f"aurora=files.mcp_files"
        )

    # -----------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------

    def _blob_key(self, org_id: str, file_id: str, filename: str) -> str:
        """Same logical layout as the Azure blob_path: {org}/{file_id}/{filename}.
        Prefixed with self._prefix so the bucket can be shared with other data
        without colliding."""
        return f"{self._prefix}/{org_id}/{file_id}/{filename}"

    def _read_record(self, file_id: str, org_id: str) -> Optional[Dict[str, Any]]:
        pool = _pg_conn()
        conn = pool.getconn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM files.mcp_files
                     WHERE file_id = %s AND org_id = %s
                     LIMIT 1
                    """,
                    (file_id, org_id),
                )
                row = cur.fetchone()
            return dict(row) if row else None
        finally:
            pool.putconn(conn)

    def _read_record_by_id(self, file_id: str) -> Optional[Dict[str, Any]]:
        """Look up a file record by file_id alone (file_id is globally unique).
        Used by the public hosted upload page, which only knows the file_id."""
        pool = _pg_conn()
        conn = pool.getconn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM files.mcp_files
                     WHERE file_id = %s
                     LIMIT 1
                    """,
                    (file_id,),
                )
                row = cur.fetchone()
            return dict(row) if row else None
        finally:
            pool.putconn(conn)

    def _touch_upload_expiry(self, file_id: str, expires_at: datetime) -> None:
        """Targeted UPDATE of upload_url_expires_at + updated_at only — avoids
        re-upserting the whole row (which would double-encode the JSON columns)."""
        pool = _pg_conn()
        conn = pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE files.mcp_files
                       SET upload_url_expires_at = %s, updated_at = %s
                     WHERE file_id = %s
                    """,
                    (expires_at, datetime.now(timezone.utc), file_id),
                )
            conn.commit()
        finally:
            pool.putconn(conn)

    async def regenerate_upload_url(self, file_id: str) -> Optional[Dict[str, Any]]:
        """Re-sign a fresh presigned PUT URL for a still-pending upload, keyed on
        file_id alone.

        Powers the hosted upload page: the page fetches this on load instead of
        carrying a ~700-char presigned URL in the link fragment (LLMs truncate
        that, which broke uploads after the Azure→AWS move — SigV4 URLs are longer
        than Azure SAS). Each page load gets a fresh 30-min window. Returns None
        if the file is unknown or already uploaded.
        """
        rec = self._read_record_by_id(file_id)
        if not rec or rec.get("status") != "pending_upload":
            return None
        blob_path = rec.get("blob_path")
        if not blob_path:
            return None
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=UPLOAD_URL_TTL_MINUTES)
        upload_url = self._s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self._bucket,
                "Key": blob_path,
            },
            ExpiresIn=UPLOAD_URL_TTL_MINUTES * 60,
            HttpMethod="PUT",
        )
        try:
            self._touch_upload_expiry(file_id, expires_at)
        except Exception as e:
            # Non-fatal: the presigned URL itself is valid regardless of the
            # informational expiry column.
            logger.warning("regenerate_upload_url: expiry slide failed for %s: %s", file_id, e)
        return {
            "file_id": file_id,
            "filename": rec.get("filename"),
            "upload_url": upload_url,
            "expires_at": expires_at.isoformat(),
            "max_size_bytes": MAX_FILE_SIZE_BYTES.get(
                rec.get("file_type"), MAX_FILE_SIZE_BYTES["custom"]
            ),
        }

    def _upsert_record(self, record: Dict[str, Any]) -> None:
        """INSERT-or-UPDATE the file record. Field names match the Aurora
        files.mcp_files columns (which mirror the Cosmos doc keys)."""
        pool = _pg_conn()
        conn = pool.getconn()
        try:
            cols = [
                "file_id", "org_id", "user_id", "filename", "file_type",
                "content_hash", "size_bytes",
                "blob_path", "blob_url", "upload_source", "upload_url_expires_at",
                "status", "created_at", "updated_at", "expires_at",
                "parent_file_id",
                "linked_job_ids", "linked_tool_calls",
                "metadata", "processing_results",
            ]
            vals = [
                record.get("file_id") or record.get("id"),
                record.get("org_id"),
                record.get("user_id"),
                record.get("filename"),
                record.get("file_type"),
                record.get("content_hash"),
                record.get("size_bytes"),
                record.get("blob_path"),
                record.get("blob_url"),
                record.get("upload_source"),
                record.get("upload_url_expires_at"),
                record.get("status"),
                record.get("created_at"),
                record.get("updated_at"),
                record.get("expires_at"),
                record.get("parent_file_id"),
                json.dumps(record.get("linked_job_ids") or []),
                json.dumps(record.get("linked_tool_calls") or []),
                json.dumps(record.get("metadata")) if record.get("metadata") is not None else None,
                json.dumps(record.get("processing_results")) if record.get("processing_results") is not None else None,
            ]
            placeholders = ", ".join(["%s"] * len(cols))
            updates = ", ".join(
                f"{c} = EXCLUDED.{c}" for c in cols if c != "file_id"
            )
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO files.mcp_files ({', '.join(cols)})
                    VALUES ({placeholders})
                    ON CONFLICT (file_id) DO UPDATE SET {updates}
                    """,
                    vals,
                )
            conn.commit()
        finally:
            pool.putconn(conn)

    # -----------------------------------------------------------------
    # Generate upload URL
    # -----------------------------------------------------------------

    async def generate_upload_url(
        self,
        org_id: str,
        user_id: str,
        filename: str,
        file_type: str = "custom",
        upload_source: str = "api",
        metadata: Optional[Dict[str, Any]] = None,
        ttl_days: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create a file record and return an S3 presigned PUT URL."""
        file_id = f"f-{uuid.uuid4().hex}"
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=UPLOAD_URL_TTL_MINUTES)

        if file_type not in ALLOWED_FILE_TYPES:
            raise ValueError(
                f"Unknown file_type: {file_type}. "
                f"Allowed: {list(ALLOWED_FILE_TYPES.keys())}"
            )

        ext = ""
        if "." in filename:
            ext = "." + filename.rsplit(".", 1)[-1].lower()
        allowed_exts = ALLOWED_FILE_TYPES[file_type]
        if allowed_exts and ext not in allowed_exts:
            raise ValueError(
                f"Extension '{ext}' not allowed for file_type '{file_type}'. "
                f"Allowed: {sorted(allowed_exts)}"
            )

        # Same logical layout as the Azure version
        blob_path = self._blob_key(org_id, file_id, filename)

        upload_url = self._s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self._bucket,
                "Key": blob_path,
            },
            ExpiresIn=UPLOAD_URL_TTL_MINUTES * 60,
            HttpMethod="PUT",
        )

        if ttl_days is not None:
            file_expires = now + timedelta(days=ttl_days)
        elif ttl_days is None and FILE_TTL_DAYS_PAID is None:
            file_expires = None
        else:
            file_expires = now + timedelta(days=FILE_TTL_DAYS_FREE)

        record = {
            "file_id": file_id,
            "org_id": org_id,
            "user_id": user_id,
            "filename": filename,
            "file_type": file_type,
            "upload_source": upload_source,
            "blob_url": f"s3://{self._bucket}/{blob_path}",
            "blob_path": blob_path,
            "status": "pending_upload",
            "size_bytes": None,
            "content_hash": None,
            "parent_file_id": None,
            "linked_tool_calls": [],
            "linked_job_ids": [],
            "processing_results": None,
            "metadata": metadata or {},
            "created_at": now,
            "updated_at": now,
            "expires_at": file_expires,
            "upload_url_expires_at": expires_at,
        }
        self._upsert_record(record)
        logger.info(
            f"File record created: {file_id} ({file_type}: {filename}) "
            f"for org {org_id}"
        )

        return {
            "file_id": file_id,
            "upload_url": upload_url,
            "expires_at": expires_at.isoformat(),
            "file_id_display": file_id[:10],
            "max_size_bytes": MAX_FILE_SIZE_BYTES.get(file_type, MAX_FILE_SIZE_BYTES["custom"]),
            "blob_path": blob_path,
        }

    # -----------------------------------------------------------------
    # Confirm upload (called after client PUTs the file)
    # -----------------------------------------------------------------

    async def confirm_upload(self, file_id: str, org_id: str) -> Dict[str, Any]:
        record = self._read_record(file_id, org_id)
        if not record:
            raise FileNotFoundError(f"File record {file_id} not found")

        blob_path = record.get("blob_path", "")

        try:
            head = self._s3.head_object(Bucket=self._bucket, Key=blob_path)
            size = head["ContentLength"]
        except Exception as e:
            return {
                "file_id": file_id,
                "status": "pending_upload",
                "message": f"File not yet uploaded: {e}",
            }

        # Compute a content-deterministic SHA-256 over the uploaded body.
        # The S3 ETag was used previously but is NOT a content digest for
        # SSE-KMS objects (the bucket is novomcp-files, SSE-KMS encrypted) —
        # each upload generates a unique data key, so identical bytes can
        # produce different ETags. SHA-256 server-side guarantees identical
        # content → identical content_hash, which is the precondition for
        # (org_id, content_hash) dedup in Aurora.
        #
        # Hash budget: stream in chunks; cap at CONTENT_HASH_MAX_SIZE so a
        # multi-GB upload doesn't pin the worker. Files above the cap are
        # confirmed without a hash (status flips to "uploaded" but dedup
        # cannot fire for them — acceptable tradeoff for large trajectories).
        content_hash = None
        if size <= CONTENT_HASH_MAX_SIZE:
            try:
                import hashlib
                hasher = hashlib.sha256()
                body_stream = self._s3.get_object(Bucket=self._bucket, Key=blob_path)["Body"]
                for chunk in iter(lambda: body_stream.read(_HASH_CHUNK_SIZE), b""):
                    hasher.update(chunk)
                content_hash = hasher.hexdigest()
            except Exception as e:
                logger.warning(
                    "content_hash computation failed for %s (%s bytes): %s",
                    file_id, size, e,
                )
                # Fall through — dedup skipped for this row, but the file is
                # still confirmed as uploaded so the auto-process chain runs.

        now = datetime.now(timezone.utc)
        record["status"] = "uploaded"
        record["size_bytes"] = size
        record["content_hash"] = content_hash
        record["updated_at"] = now
        self._upsert_record(record)

        logger.info(f"File upload confirmed: {file_id} ({size} bytes)")

        auto_process = (record.get("metadata") or {}).get("auto_process")
        estimated_minutes = None
        if auto_process:
            estimated_minutes = estimate_processing_time(
                record.get("file_type", "custom"), size
            )
            record["status"] = "processing"
            record["updated_at"] = datetime.now(timezone.utc)
            md = dict(record.get("metadata") or {})
            md["estimated_processing_minutes"] = estimated_minutes
            record["metadata"] = md
            self._upsert_record(record)
            logger.info(
                f"File {file_id} has auto-process instructions: "
                f"tool={auto_process.get('tool')}, "
                f"estimated={estimated_minutes} min"
            )

        return {
            "file_id": file_id,
            "status": "processing" if auto_process else "uploaded",
            "size_bytes": size,
            "content_hash": content_hash,
            "filename": record.get("filename"),
            "file_type": record.get("file_type"),
            "auto_process": auto_process,
            "estimated_processing_minutes": estimated_minutes,
        }

    # -----------------------------------------------------------------
    # Get file status
    # -----------------------------------------------------------------

    async def get_file_status(
        self, file_id: str, org_id: str
    ) -> Optional[Dict[str, Any]]:
        record = self._read_record(file_id, org_id)
        if not record:
            return None

        if record.get("status") == "pending_upload":
            confirmed = await self.confirm_upload(file_id, org_id)
            if confirmed.get("status") == "uploaded":
                record = self._read_record(file_id, org_id)

        # Normalize datetime → isoformat for the response shape callers expect.
        def _iso(v):
            if isinstance(v, datetime):
                return v.isoformat()
            return v

        return {
            "file_id": record["file_id"],
            "filename": record.get("filename"),
            "file_type": record.get("file_type"),
            "status": record.get("status"),
            "size_bytes": record.get("size_bytes"),
            "upload_source": record.get("upload_source"),
            "content_hash": record.get("content_hash"),
            "parent_file_id": record.get("parent_file_id"),
            "linked_tool_calls": record.get("linked_tool_calls") or [],
            "linked_job_ids": record.get("linked_job_ids") or [],
            "processing_results": record.get("processing_results"),
            "metadata": record.get("metadata") or {},
            "created_at": _iso(record.get("created_at")),
            "updated_at": _iso(record.get("updated_at")),
            "expires_at": _iso(record.get("expires_at")),
        }

    # -----------------------------------------------------------------
    # List files
    # -----------------------------------------------------------------

    async def list_files(
        self,
        org_id: str,
        file_type: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        pool = _pg_conn()
        conn = pool.getconn()
        try:
            where = ["org_id = %s"]
            params: List[Any] = [org_id]
            if file_type:
                where.append("file_type = %s")
                params.append(file_type)
            if status:
                where.append("status = %s")
                params.append(status)
            params.append(int(limit))
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"""
                    SELECT file_id, filename, file_type, status, size_bytes,
                           upload_source, created_at, expires_at, linked_job_ids
                      FROM files.mcp_files
                     WHERE {' AND '.join(where)}
                     ORDER BY created_at DESC NULLS LAST
                     LIMIT %s
                    """,
                    tuple(params),
                )
                rows = cur.fetchall()
        finally:
            pool.putconn(conn)

        def _iso(v):
            return v.isoformat() if isinstance(v, datetime) else v

        files = []
        for row in rows:
            files.append({
                "file_id": row["file_id"],
                "filename": row.get("filename"),
                "file_type": row.get("file_type"),
                "status": row.get("status"),
                "size_bytes": row.get("size_bytes"),
                "upload_source": row.get("upload_source"),
                "created_at": _iso(row.get("created_at")),
                "expires_at": _iso(row.get("expires_at")),
                "linked_job_ids": row.get("linked_job_ids") or [],
            })

        return {
            "files": files,
            "total": len(files),
            "org_id": org_id,
            "filters": {
                "file_type": file_type,
                "status": status,
                "limit": limit,
            },
        }

    # -----------------------------------------------------------------
    # Fetch file content (for downstream tool consumption)
    # -----------------------------------------------------------------

    async def fetch_file_content(
        self, file_id: str, org_id: str
    ) -> bytes:
        record = self._read_record(file_id, org_id)
        if not record:
            raise FileNotFoundError(f"File {file_id} not found")

        if record.get("status") not in ("uploaded", "processing", "completed"):
            raise ValueError(
                f"File {file_id} is not ready (status: {record.get('status')}). "
                f"Upload must complete before the file can be used."
            )

        blob_path = record.get("blob_path", "")
        try:
            obj = self._s3.get_object(Bucket=self._bucket, Key=blob_path)
            return obj["Body"].read()
        except Exception as e:
            raise IOError(f"Failed to fetch file {file_id} from storage: {e}")

    # -----------------------------------------------------------------
    # Link tool call / job to a file record
    # -----------------------------------------------------------------

    async def link_tool_call(
        self,
        file_id: str,
        org_id: str,
        tool_name: str,
        job_id: Optional[str] = None,
    ) -> None:
        record = self._read_record(file_id, org_id)
        if not record:
            return

        now = datetime.now(timezone.utc)
        calls = list(record.get("linked_tool_calls") or [])
        calls.append({"tool": tool_name, "timestamp": now.isoformat(), "job_id": job_id})
        record["linked_tool_calls"] = calls

        if job_id:
            jobs = list(record.get("linked_job_ids") or [])
            if job_id not in jobs:
                jobs.append(job_id)
            record["linked_job_ids"] = jobs

        record["updated_at"] = now
        self._upsert_record(record)

    # -----------------------------------------------------------------
    # Create child file (output provenance)
    # -----------------------------------------------------------------

    async def create_output_file(
        self,
        parent_file_id: str,
        org_id: str,
        user_id: str,
        filename: str,
        file_type: str,
        content: bytes,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        file_id = f"f-{uuid.uuid4().hex}"
        now = datetime.now(timezone.utc)
        blob_path = self._blob_key(org_id, file_id, filename)

        self._s3.put_object(Bucket=self._bucket, Key=blob_path, Body=content)

        content_hash = hashlib.sha256(content).hexdigest()

        record = {
            "file_id": file_id,
            "org_id": org_id,
            "user_id": user_id,
            "filename": filename,
            "file_type": file_type,
            "upload_source": "tool_output",
            "blob_url": f"s3://{self._bucket}/{blob_path}",
            "blob_path": blob_path,
            "status": "completed",
            "size_bytes": len(content),
            "content_hash": content_hash,
            "parent_file_id": parent_file_id,
            "linked_tool_calls": [],
            "linked_job_ids": [],
            "processing_results": None,
            "metadata": metadata or {},
            "created_at": now,
            "updated_at": now,
            "expires_at": None,  # Output files don't expire
        }
        self._upsert_record(record)
        logger.info(
            f"Output file created: {file_id} ({filename}) parent={parent_file_id}"
        )

        return {
            "file_id": file_id,
            "filename": filename,
            "file_type": file_type,
            "size_bytes": len(content),
            "parent_file_id": parent_file_id,
        }

    # -----------------------------------------------------------------
    # Generate download URL (for output files)
    # -----------------------------------------------------------------

    async def generate_download_url(
        self, file_id: str, org_id: str, ttl_minutes: int = 60
    ) -> Optional[str]:
        record = self._read_record(file_id, org_id)
        if not record:
            return None

        blob_path = record.get("blob_path", "")
        return self._s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": blob_path},
            ExpiresIn=int(ttl_minutes * 60),
            HttpMethod="GET",
        )

    # -----------------------------------------------------------------
    # Content-hash dedup
    # -----------------------------------------------------------------

    async def check_dedup(
        self, org_id: str, content_hash: str
    ) -> Optional[str]:
        pool = _pg_conn()
        conn = pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT file_id FROM files.mcp_files
                     WHERE org_id = %s AND content_hash = %s
                       AND status IN ('uploaded', 'processing', 'completed')
                     LIMIT 1
                    """,
                    (org_id, content_hash),
                )
                row = cur.fetchone()
            return row[0] if row else None
        finally:
            pool.putconn(conn)

    # -----------------------------------------------------------------
    # Auto-process completion — update file record with results
    # -----------------------------------------------------------------

    async def complete_processing(
        self,
        file_id: str,
        org_id: str,
        results: Dict[str, Any],
        output_files: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        record = self._read_record(file_id, org_id)
        if not record:
            return

        now = datetime.now(timezone.utc)
        record["status"] = "completed"
        record["processing_results"] = results
        record["updated_at"] = now

        if output_files:
            md = dict(record.get("metadata") or {})
            md["output_files"] = output_files
            record["metadata"] = md

        self._upsert_record(record)
        logger.info(f"File processing complete: {file_id}")

    async def fail_processing(
        self,
        file_id: str,
        org_id: str,
        error: str,
    ) -> None:
        record = self._read_record(file_id, org_id)
        if not record:
            return

        now = datetime.now(timezone.utc)
        record["status"] = "failed"
        record["processing_results"] = {"error": error}
        record["updated_at"] = now
        self._upsert_record(record)
        logger.info(f"File processing failed: {file_id} — {error[:100]}")


# =====================================================================
# Processing Time Estimation (module-level, no client needed)
# =====================================================================

_PROCESSING_TIME_ESTIMATES = {
    "qm_log":     (3, 0.5),
    "pdb":        (2, 0.1),
    "trajectory": (5, 0.01),
    "library":    (1, 0.05),
    "frcmod":     (0.5, 0),
    "custom":     (5, 0.5),
}


def estimate_processing_time(file_type: str, size_bytes: int) -> int:
    base, factor = _PROCESSING_TIME_ESTIMATES.get(
        file_type, _PROCESSING_TIME_ESTIMATES["custom"]
    )
    size_mb = (size_bytes or 0) / (1024 * 1024)
    estimated = base + (size_mb * factor)
    return max(1, int(estimated + 0.5))
