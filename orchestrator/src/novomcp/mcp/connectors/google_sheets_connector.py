"""
Google Sheets connector adapter for Connection Registry.

Uses gspread + google-auth for connection testing, schema discovery
(header row + type inference), and data writing via append/update.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from .base import (
    BaseConnector,
    ConnectorType,
    ExportResult,
    NormalizedType,
    SchemaColumn,
    TargetSchema,
    WriteMode,
)

logger = logging.getLogger(__name__)


def _infer_type(value: Any) -> NormalizedType:
    """Infer normalized type from a cell value."""
    if value is None or value == "":
        return NormalizedType.STRING
    if isinstance(value, bool):
        return NormalizedType.BOOLEAN
    if isinstance(value, (int, float)):
        return NormalizedType.NUMBER
    s = str(value).strip()
    # Try number
    try:
        float(s.replace(",", ""))
        return NormalizedType.NUMBER
    except ValueError:
        pass
    # Try boolean
    if s.lower() in ("true", "false", "yes", "no"):
        return NormalizedType.BOOLEAN
    return NormalizedType.STRING


class GoogleSheetsConnector(BaseConnector):
    """Google Sheets connector via service account."""

    connector_type = ConnectorType.GOOGLE_SHEETS

    def __init__(self, config: Dict[str, Any], credentials: Dict[str, Any]):
        super().__init__(config, credentials)
        self._client = None
        self._spreadsheet = None

    def _get_client(self):
        if self._client is not None:
            return self._client

        import gspread
        from google.oauth2.service_account import Credentials

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]

        # credentials["service_account_json"] is the service account key dict
        sa_info = self.credentials["service_account_json"]
        if isinstance(sa_info, str):
            sa_info = json.loads(sa_info)

        creds = Credentials.from_service_account_info(sa_info, scopes=scopes)
        self._client = gspread.authorize(creds)
        return self._client

    def _get_spreadsheet(self):
        if self._spreadsheet is not None:
            return self._spreadsheet

        client = self._get_client()
        self._spreadsheet = client.open_by_key(self.config["spreadsheet_id"])
        return self._spreadsheet

    async def test_connection(self) -> Tuple[bool, Optional[str]]:
        try:
            spreadsheet = self._get_spreadsheet()
            _ = spreadsheet.title
            return True, None
        except Exception as e:
            return False, str(e)

    async def discover_schema(self) -> List[TargetSchema]:
        try:
            spreadsheet = self._get_spreadsheet()
            schemas = []

            for worksheet in spreadsheet.worksheets():
                try:
                    # Get header row
                    headers = worksheet.row_values(1)
                    if not headers:
                        continue

                    # Get sample data (up to 100 rows) for type inference
                    sample_data = worksheet.get_all_values()
                    data_rows = sample_data[1:101] if len(sample_data) > 1 else []

                    columns = []
                    for i, header in enumerate(headers):
                        if not header.strip():
                            continue

                        # Infer type from sample values
                        col_values = [row[i] for row in data_rows if i < len(row) and row[i]]
                        if col_values:
                            types = [_infer_type(v) for v in col_values[:20]]
                            # Majority vote
                            most_common = max(set(types), key=types.count)
                        else:
                            most_common = NormalizedType.STRING

                        columns.append(
                            SchemaColumn(
                                name=header.strip(),
                                data_type=most_common,
                                native_type="TEXT",
                                nullable=True,
                            )
                        )

                    schemas.append(
                        TargetSchema(
                            name=worksheet.title,
                            columns=columns,
                            location=f"SHEETS:{self.config['spreadsheet_id']}:{worksheet.title}",
                            metadata={
                                "connector": "google_sheets",
                                "row_count": len(sample_data) - 1 if sample_data else 0,
                                "worksheet_id": worksheet.id,
                            },
                        )
                    )
                except Exception as e:
                    logger.warning(f"Failed to discover schema for worksheet {worksheet.title}: {e}")

            return schemas

        except Exception as e:
            logger.error(f"Google Sheets schema discovery failed: {e}")
            return []

    async def write_data(
        self,
        target: str,
        data: List[Dict[str, Any]],
        mode: WriteMode = WriteMode.APPEND,
    ) -> ExportResult:
        if not data:
            return ExportResult(success=True, rows_written=0, target_location=target)

        try:
            spreadsheet = self._get_spreadsheet()

            # Find or create worksheet
            try:
                worksheet = spreadsheet.worksheet(target)
            except Exception:
                worksheet = spreadsheet.add_worksheet(title=target, rows=1000, cols=26)

            columns = list(data[0].keys())

            if mode == WriteMode.REPLACE:
                worksheet.clear()
                # Write header
                worksheet.append_row(columns)

            elif mode == WriteMode.APPEND:
                # Check if headers exist
                existing_headers = worksheet.row_values(1)
                if not existing_headers:
                    worksheet.append_row(columns)

            # Convert data to rows
            rows = []
            for row in data:
                row_values = []
                for col in columns:
                    val = row.get(col)
                    if val is None:
                        row_values.append("")
                    elif isinstance(val, (dict, list)):
                        row_values.append(json.dumps(val))
                    else:
                        row_values.append(val)
                rows.append(row_values)

            # Batch append
            worksheet.append_rows(rows, value_input_option="USER_ENTERED")

            location = f"SHEETS:{self.config['spreadsheet_id']}:{target}"
            return ExportResult(
                success=True,
                rows_written=len(rows),
                target_location=location,
                details={"spreadsheet_id": self.config["spreadsheet_id"], "worksheet": target},
            )

        except Exception as e:
            logger.error(f"Google Sheets write failed: {e}")
            return ExportResult(
                success=False,
                error=str(e),
                target_location=f"SHEETS:{target}",
            )

    @staticmethod
    def get_config_schema() -> dict:
        return {
            "type": "object",
            "required": ["spreadsheet_id"],
            "properties": {
                "spreadsheet_id": {"type": "string", "description": "Google Sheets spreadsheet ID (from URL)"},
            },
        }

    @staticmethod
    def get_credentials_schema() -> dict:
        return {
            "type": "object",
            "required": ["service_account_json"],
            "properties": {
                "service_account_json": {
                    "type": "object",
                    "description": "Google service account key JSON (the full JSON key file content)",
                },
            },
        }

    async def close(self):
        self._client = None
        self._spreadsheet = None
