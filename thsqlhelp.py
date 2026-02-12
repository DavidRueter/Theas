"""
Utilities to help pass literals into SQL.

body_to_sql_hex()
Converts HTTP request.body to a hex string, using request.headers to determine
character encoding of the body bytes (UTF-8, ASCII, etc.), decompressing
the payload as needed, and re-encodes the body payload as
UCS-2/UTF-16LE (i.e. for MSSQL nvarchar(MAX)) and returns a hex string literal.

Also returns body_type (text, textJSON, binary, etc.) and meta (BodyMeta
with details of the body.)

is_numeric(), is_char(), is_date(), is_binary()
For conveniently determining the type of data indicated by the SQL datatype

to_mssql_literal()
Accepts a variable of almost any type, and the target SQL datatype,
and returns an appropriate literal.

quotestr()
Accepts a string, replaces embedded single quotes with pairs of single quotes,
and then encloses the string in single quotes. Used internally, but useful
elsewhere too.

https://chatgpt.com/c/689b94d1-d848-832d-a8a6-b187a1f0a3cd  Body encoding in Tornado
https://chatgpt.com/c/68a1d388-41b4-832e-b9ac-74e81a5cd45e  Bytes constructor behavior

https://chatgpt.com/c/68a3485c-be98-8326-b7ac-df03437cd69e  Module review
https://chatgpt.com/c/68a368aa-30ac-8330-9761-e282a72e81fb  Module Review
https://chatgpt.com/c/68a3764b-b9a8-8322-bf4a-74ecbebbee03  Module Review
https://chatgpt.com/c/68a37ea2-47ac-832d-8426-a5fa7e27b189  Module Review

https://chatgpt.com/c/68a35bb3-7aa0-832b-a5a5-a6accad97ae7  Python Unicode storage
https://chatgpt.com/c/68a3af11-8200-832f-9424-bca0efb32e0f  Python Unicode and FreeTDS to SQL encoding

https://chatgpt.com/c/68a39d05-e278-832d-bcbe-b1c3ee4a524a  Validation of JSON
"""

from __future__ import annotations

import gzip
import re
import unicodedata
import zlib
from typing import Dict, List, Mapping, Optional, Tuple, TypedDict, Union

import json

# Optional brotli support
try:
    import brotli as _brotli  # type: ignore
except Exception:  # pragma: no cover
    _brotli = None

__all__ = [
    "body_to_sql_hex",
    "BodyMeta",
    "to_utf16le_bytes",

    "is_numeric",
    "is_char",
    "is_date",
    "is_binary",
    "to_mssql_literal",

    "quotestr"
]

import base64
import datetime as dt
import decimal
import uuid
import enum
import pathlib
import dataclasses

# -------------------------
# body_to_sql_hex() and helpers
# -------------------------
"""
body_to_sql_hex() and helpers

Purpose
-------
Convert an HTTP request body (raw bytes) into a SQL Server–safe literal for use
in EXEC statements (e.g., `EXEC dbo.MyProc @Body=<literal>, @BodyType=<...>`).

API
---
body_to_sql_hex(
    body, headers, *,
    empty_as_null=True, strict=False,
    max_body_bytes=None, decompress_limit_bytes=8*1024*1024,
) -> (literal: str, body_type: str, meta: BodyMeta)

Return values:
- `literal`   : "0x<HEX>" or "NULL"
- `body_type` : one of {'text','textJSON','textXML','ascii_text','binary','binaryMultipart','none'}
- `meta`      : dict (TypedDict `BodyMeta`) with diagnostic details.
               All keys are always present; values may be None/False/"".

Body type semantics
-------------------
- 'text'           : generic textual payload (normalized, UTF-16LE; safe to CAST(@Body AS NVARCHAR(MAX)))
- 'textJSON'       : JSON payload (normalized, UTF-16LE)
- 'textXML'        : XML payload (normalized, UTF-16LE)
- 'ascii_text'     : pure 7-bit ASCII (bytes preserved; does NOT start with "text")
- 'binary'         : arbitrary binary (raw bytes preserved)
- 'binaryMultipart': multipart payload (raw bytes preserved)
- 'none'           : empty body → "NULL"

Behavior summary
----------------
• Textual media (text/*, application/json, application/xml, application/x-www-form-urlencoded, +json/+xml):
    - If pure ASCII (or declared us-ascii), returns 'ascii_text' with raw bytes.
    - Otherwise decodes, NFC-normalizes, re-encodes as UTF-16LE, returns 'text' / 'textJSON' / 'textXML'.

• Binary / multipart (multipart/*, application/octet-stream, image/*, audio/*, video/*, pdf, etc.):
    - Returns 'binary' or 'binaryMultipart' with raw bytes hex.

• Empty body:
    - ("NULL","none") if empty_as_null=True (default).
    - ("0x","binary") if empty_as_null=False.

• Content-Encoding:
    - gzip, deflate, br decompressed with safety limit (default 8 MB).

Usage in T-SQL
--------------
Typical stored procedure pattern:

    IF @Body IS NULL RETURN;

    DECLARE @BodyNchar NVARCHAR(MAX) = NULL;

    IF @BodyType LIKE 'text%'  -- text, textJSON, textXML
    BEGIN
      SET @BodyNchar = CAST(@Body AS NVARCHAR(MAX));
    END
    ELSE IF @BodyType = 'ascii_text'
    BEGIN
      -- VARBINARY -> VARCHAR conversion
      DECLARE @BodyChar VARCHAR(MAX) =
        CONVERT(VARCHAR(MAX), @Body) COLLATE Latin1_General_100_CI_AS;
    END
    ELSE IF @BodyType = 'binary'
    BEGIN
      -- Raw binary handling
    END
    ELSE IF @BodyType = 'binaryMultipart'
    BEGIN
      -- Multipart handling
    END

Common U.S. media types and results
-----------------------------------
- application/json; charset=utf-8     → textJSON
- text/plain; charset=utf-8           → text
- text/plain; charset=us-ascii        → ascii_text
- application/x-www-form-urlencoded   → text
- application/xml / text/xml          → textXML
- multipart/form-data                 → binaryMultipart
- application/octet-stream            → binary
- image/*, audio/*, video/*, pdf      → binary

Security notes
--------------
- Hex expansion doubles payload size; enforce max_body_bytes and decompress_limit_bytes.
- This function does not perform SQL escaping; it produces 0x<HEX> literals or NULL.
"""

# -------------------------
# Media-type classification
# -------------------------

_TEXTUAL_BASES = (
    "application/json",
    "application/x-www-form-urlencoded",
    "application/xml",
    "application/javascript",
)
_JSON_XML_SUFFIXES = ("+json", "+xml")

# Types that we hard-classify as binary
_BINARY_PREFIXES = ("image/", "audio/", "video/")
_BINARY_EXACT = {
    "application/octet-stream",
    "application/pdf",
    "application/zip",
    "application/gzip",
    "application/x-7z-compressed",
    "application/x-rar-compressed",
}

# XML declaration encoding sniff
_XML_DECL_RE = re.compile(
    rb'^\s*<\?xml[^>]*encoding=["\']([A-Za-z0-9._-]+)["\']', re.IGNORECASE
)

# Known BOMs for quick sniffing (maps to Python codec names)
_BOMS: List[Tuple[bytes, str]] = [
    (b"\xEF\xBB\xBF", "utf-8-sig"),
    (b"\xFF\xFE\x00\x00", "utf-32-le"),
    (b"\x00\x00\xFE\xFF", "utf-32-be"),
    (b"\xFF\xFE", "utf-16-le"),
    (b"\xFE\xFF", "utf-16-be"),
]

# SQL Server varbinary(MAX) / nvarchar(MAX) maximum length:
# 2^31 - 1 bytes = 2,147,483,647 (~2 GB)
_SQLSERVER_MAX_BYTES = 2_147_483_647


# -------------------------
# TypedDict for meta
# -------------------------

class BodyMeta(TypedDict):
    content_type_raw: str
    content_encoding_raw: str
    media_type: str
    charset_header: Optional[str]
    charset_used: Optional[str]
    content_encodings: List[str]
    removed_encodings: List[str]
    used_bom: Optional[str]
    used_xml_decl: bool
    normalized: bool
    decompressed: bool
    body_len_input: int
    body_len_final: int
    body_type: str


def _build_meta(
        *,
        content_type_raw: str = "",
        content_encoding_raw: str = "",
        media_type: str = "",
        charset_header: Optional[str] = None,
        charset_used: Optional[str] = None,
        content_encodings: Optional[List[str]] = None,
        removed_encodings: Optional[List[str]] = None,
        used_bom: Optional[str] = None,
        used_xml_decl: bool = False,
        normalized: bool = False,
        decompressed: bool = False,
        body_len_input: int = 0,
        body_len_final: int = 0,
        body_type: str = "",
) -> BodyMeta:
    """Single factory to ensure meta always contains the full, consistent shape."""
    return BodyMeta(
        content_type_raw=content_type_raw or "",
        content_encoding_raw=content_encoding_raw or "",
        media_type=media_type or "",
        charset_header=charset_header,
        charset_used=charset_used,
        content_encodings=list(content_encodings or []),
        removed_encodings=list(removed_encodings or []),
        used_bom=used_bom,
        used_xml_decl=bool(used_xml_decl),
        normalized=bool(normalized),
        decompressed=bool(decompressed),
        body_len_input=int(body_len_input),
        body_len_final=int(body_len_final),
        body_type=body_type or "",
    )


# -------------------------
# Header helpers
# -------------------------

def _normalize_headers(headers: Union[Mapping, object]) -> Dict[str, str]:
    """Build a case-insensitive dict of headers for easy access."""
    out: Dict[str, str] = {}
    if headers is None:
        return out

    if hasattr(headers, "items"):
        try:
            for k, v in headers.items():  # Tornado HTTPHeaders supports items()
                out[str(k).lower()] = str(v)
            return out
        except Exception:
            pass

    for name in ("Content-Type", "content-type"):
        try:
            val = headers.get(name)  # type: ignore[attr-defined]
            if val is not None:
                out["content-type"] = str(val)
                break
        except Exception:
            pass

    for name in ("Content-Encoding", "content-encoding"):
        try:
            val = headers.get(name)  # type: ignore[attr-defined]
            if val is not None:
                out["content-encoding"] = str(val)
                break
        except Exception:
            pass

    return out


def _get_content_type_and_charset(hdrs: Mapping[str, str]) -> Tuple[str, Optional[str], str]:
    """Extract (media_type, charset, raw_content_type) from normalized headers."""
    raw_ct = hdrs.get("content-type", "")
    if not raw_ct:
        return "", None, ""
    parts = [p.strip() for p in raw_ct.split(";")]
    media = parts[0].lower()
    charset = None
    for p in parts[1:]:
        if p.lower().startswith("charset="):
            charset = p.split("=", 1)[1].strip().strip('"').strip("'").lower()
            break
    return media, charset, raw_ct


def _get_content_encodings(hdrs: Mapping[str, str]) -> Tuple[List[str], str]:
    """Parse Content-Encoding into a list of tokens and return the raw header value."""
    raw = hdrs.get("content-encoding", "")
    if not raw:
        return [], ""
    tokens = [t.strip().lower() for t in raw.split(",") if t.strip()]
    return tokens, raw


# -------------------------
# Classification & defaults
# -------------------------

def _is_textual(media_type: str) -> bool:
    if not media_type:
        return False
    if media_type.startswith("text/"):
        return True
    if media_type in _TEXTUAL_BASES:
        return True
    if any(media_type.endswith(sfx) for sfx in _JSON_XML_SUFFIXES):
        return True
    return False


def _is_binary(media_type: str) -> bool:
    if not media_type:
        return False
    if media_type in _BINARY_EXACT:
        return True
    if any(media_type.startswith(pfx) for pfx in _BINARY_PREFIXES):
        return True
    if media_type.startswith("multipart/"):
        return True
    return False


def _default_charset_for(media_type: str, body: bytes) -> str:
    if not media_type:
        return "utf-8"
    if media_type == "application/json" or media_type.endswith("+json"):
        return "utf-8"
    if media_type in ("application/xml", "text/xml") or media_type.endswith("+xml"):
        m = _XML_DECL_RE.match(body or b"")
        if m:
            return m.group(1).decode("ascii", "ignore").lower() or "utf-8"
        return "utf-8"
    if media_type == "application/x-www-form-urlencoded":
        return "utf-8"
    if media_type.startswith("text/"):
        return "utf-8"
    return "utf-8"


def _sniff_bom_charset(body: bytes) -> Optional[str]:
    for bom, enc in _BOMS:
        if body.startswith(bom):
            return enc
    return None


# -------------------------
# Content-Encoding handling
# -------------------------

def _decompress_content(body: bytes, encodings: List[str], limit: int) -> Tuple[bytes, List[str]]:
    """
    Decompress body according to Content-Encoding list (applied in order).
    We reverse the list to undo encodings in the opposite order.
    Enforces a hard limit on decompressed size to avoid zip bombs.
    Returns (decompressed_body, removed_encodings)
    """
    removed: List[str] = []
    if not encodings:
        return body, removed

    data = body
    for enc in reversed(encodings):
        if enc == "gzip":
            data = gzip.decompress(data)
            removed.append(enc)
        elif enc == "deflate":
            try:
                data = zlib.decompress(data)
            except zlib.error:
                data = zlib.decompress(data, -zlib.MAX_WBITS)
            removed.append(enc)
        elif enc in ("br", "brotli"):
            if _brotli is None:
                raise RuntimeError("Content-Encoding 'br' present but brotli is not installed")
            data = _brotli.decompress(data)
            removed.append(enc)
        elif enc == "identity":
            removed.append(enc)  # no-op
        else:
            raise RuntimeError(f"Unsupported Content-Encoding: {enc}")

        if limit is not None and len(data) > limit:
            raise RuntimeError("Decompressed body exceeds safety limit")

    return data, removed


# -------------------------
# ASCII detection
# -------------------------

def _is_pure_ascii(data: bytes) -> bool:
    try:
        data.decode("ascii")
        return True
    except UnicodeDecodeError:
        return False


def body_to_sql_hex(
        body: Union[bytes, bytearray, None],
        headers: Union[Mapping, object],
        *,
        empty_as_null: bool = True,
        strict: bool = False,
        max_body_bytes: Optional[int] = None,
        decompress_limit_bytes: Optional[int] = 8 * 1024 * 1024,
) -> Tuple[str, str, BodyMeta]:
    """
    Convert an HTTP request body to a SQL Server–safe literal and a body_type tag.

    Returns
    -------
    (literal, body_type, meta)
        literal    : "0x<HEX>" or "NULL"
        body_type  : one of {'text','textJSON','textXML','ascii_text','binary','binaryMultipart','none'}
        meta       : BodyMeta (all keys always present)
    """

    # Empty
    if not body:
        literal = "NULL" if empty_as_null else "0x"
        body_type = "none" if empty_as_null else "binary"
        meta = _build_meta(
            content_type_raw="",
            content_encoding_raw="",
            media_type="",
            charset_header=None,
            charset_used=None,
            content_encodings=[],
            removed_encodings=[],
            used_bom=None,
            used_xml_decl=False,
            normalized=False,
            decompressed=False,
            body_len_input=0,
            body_len_final=0,
            body_type=body_type,
        )
        return literal, body_type, meta

    if not isinstance(body, (bytes, bytearray)):
        raise TypeError("Body must be bytes or bytearray")

    body_bytes = bytes(body)
    if max_body_bytes is not None and len(body_bytes) > max_body_bytes:
        raise RuntimeError("Body exceeds max_body_bytes limit")

    hdrs = _normalize_headers(headers)
    media_type, charset_hdr, content_type_raw = _get_content_type_and_charset(hdrs)
    encodings, content_encoding_raw = _get_content_encodings(hdrs)

    # Decompress if needed
    removed_encs: List[str] = []
    decompressed = False
    if encodings:
        body_bytes, removed_encs = _decompress_content(
            body_bytes, encodings, limit=decompress_limit_bytes or _SQLSERVER_MAX_BYTES
        )
        decompressed = True

    # Multipart?
    if media_type.startswith("multipart/"):
        hex_lit = "0x" + body_bytes.hex().upper()
        body_type = "binaryMultipart"
        meta = _build_meta(
            content_type_raw=content_type_raw,
            content_encoding_raw=content_encoding_raw,
            media_type=media_type,
            charset_header=charset_hdr,
            charset_used=None,
            content_encodings=encodings,
            removed_encodings=removed_encs,
            used_bom=None,
            used_xml_decl=False,
            normalized=False,
            decompressed=decompressed,
            body_len_input=len(body),
            body_len_final=len(body_bytes),
            body_type=body_type,
        )
        return hex_lit, body_type, meta

    # Clearly binary?
    if _is_binary(media_type):
        hex_lit = "0x" + body_bytes.hex().upper()
        body_type = "binary"
        meta = _build_meta(
            content_type_raw=content_type_raw,
            content_encoding_raw=content_encoding_raw,
            media_type=media_type,
            charset_header=charset_hdr,
            charset_used=None,
            content_encodings=encodings,
            removed_encodings=removed_encs,
            used_bom=None,
            used_xml_decl=False,
            normalized=False,
            decompressed=decompressed,
            body_len_input=len(body),
            body_len_final=len(body_bytes),
            body_type=body_type,
        )
        return hex_lit, body_type, meta

    # Textual?
    if _is_textual(media_type):
        used_bom: Optional[str] = None
        used_xml_decl = False

        charset_used = charset_hdr
        if not charset_used:
            sniff = _sniff_bom_charset(body_bytes)
            if sniff:
                charset_used = sniff
                used_bom = sniff

        if not charset_used and (
                media_type == "application/xml" or media_type.endswith("+xml") or media_type == "text/xml"
        ):
            m = _XML_DECL_RE.match(body_bytes)
            if m:
                xml_enc = m.group(1).decode("ascii", "ignore").lower()
                if xml_enc:
                    charset_used = xml_enc
                    used_xml_decl = True

        if not charset_used:
            charset_used = _default_charset_for(media_type, body_bytes)

        # ASCII fast-path: preserve bytes & classify as ascii_text
        if (charset_hdr == "us-ascii") and _is_pure_ascii(body_bytes):
            hex_lit = "0x" + body_bytes.hex().upper()
            body_type = "ascii_text"
            meta = _build_meta(
                content_type_raw=content_type_raw,
                content_encoding_raw=content_encoding_raw,
                media_type=media_type,
                charset_header=charset_hdr,
                charset_used=charset_used,
                content_encodings=encodings,
                removed_encodings=removed_encs,
                used_bom=used_bom,
                used_xml_decl=used_xml_decl,
                normalized=False,
                decompressed=decompressed,
                body_len_input=len(body),
                body_len_final=len(body_bytes),
                body_type=body_type,
            )
            return hex_lit, body_type, meta

        if _is_pure_ascii(body_bytes) and (charset_hdr in (None, "", "utf-8", "us-ascii")):
            hex_lit = "0x" + body_bytes.hex().upper()
            body_type = "ascii_text"
            meta = _build_meta(
                content_type_raw=content_type_raw,
                content_encoding_raw=content_encoding_raw,
                media_type=media_type,
                charset_header=charset_hdr,
                charset_used=charset_used,
                content_encodings=encodings,
                removed_encodings=removed_encs,
                used_bom=used_bom,
                used_xml_decl=used_xml_decl,
                normalized=False,
                decompressed=decompressed,
                body_len_input=len(body),
                body_len_final=len(body_bytes),
                body_type=body_type,
            )
            return hex_lit, body_type, meta

        # Otherwise, decode → normalize → UTF-16LE → hex
        errors = "strict" if strict else "replace"
        text = body_bytes.decode(charset_used, errors=errors)
        text = unicodedata.normalize("NFC", text)
        u16le = text.encode("utf-16le")
        hex_lit = "0x" + u16le.hex().upper()

        # Specialize body_type
        if media_type == "application/json" or media_type.endswith("+json"):
            bt = "textJSON"
        elif media_type in ("application/xml", "text/xml") or media_type.endswith("+xml"):
            bt = "textXML"
        else:
            bt = "text"

        meta = _build_meta(
            content_type_raw=content_type_raw,
            content_encoding_raw=content_encoding_raw,
            media_type=media_type,
            charset_header=charset_hdr,
            charset_used=charset_used,
            content_encodings=encodings,
            removed_encodings=removed_encs,
            used_bom=used_bom,
            used_xml_decl=used_xml_decl,
            normalized=True,
            decompressed=decompressed,
            body_len_input=len(body),
            body_len_final=len(u16le),
            body_type=bt,
        )
        return hex_lit, bt, meta

    # Unknown media → conservative binary
    hex_lit = "0x" + body_bytes.hex().upper()
    body_type = "binary"
    meta = _build_meta(
        content_type_raw=content_type_raw,
        content_encoding_raw=content_encoding_raw,
        media_type=media_type,
        charset_header=charset_hdr,
        charset_used=None,
        content_encodings=encodings,
        removed_encodings=removed_encs,
        used_bom=None,
        used_xml_decl=False,
        normalized=False,
        decompressed=decompressed,
        body_len_input=len(body),
        body_len_final=len(body_bytes),
        body_type=body_type,
    )
    return hex_lit, body_type, meta


def to_utf16le_bytes(obj, *, bom: bool = False, default=None, **json_kwargs) -> bytes:
    """
    Serialize `obj` to JSON and return UTF-16LE bytes.

    - bom: prepend 0xFF 0xFE if True.
    - default: optional user-supplied serializer (called first).
    - json_kwargs: forwarded to json.dumps (e.g., indent=2).
    """

    def _default(o):
        # Let the user-supplied default try first.
        if default is not None:
            try:
                return default(o)
            except TypeError:
                pass  # fall through to built-ins below

        # Dataclasses -> dict
        if dataclasses.is_dataclass(o):
            return dataclasses.asdict(o)

        # datetime/date/time -> ISO 8601
        if isinstance(o, (dt.datetime, dt.date, dt.time)):
            return o.isoformat()

        # Decimal -> string (preserve precision)
        if isinstance(o, decimal.Decimal):
            return str(o)

        # UUID -> string
        if isinstance(o, uuid.UUID):
            return str(o)

        # Enum -> value (fallback to name if value not JSON-serializable)
        if isinstance(o, enum.Enum):
            v = o.value
            try:
                json.dumps(v)  # quick check
                return v
            except TypeError:
                return o.name

        # Path -> string
        if isinstance(o, pathlib.Path):
            return str(o)

        # bytes/bytearray/memoryview -> base64 string
        if isinstance(o, (bytes, bytearray, memoryview)):
            return base64.b64encode(bytes(o)).decode("utf-8")

        # set/frozenset -> list
        if isinstance(o, (set, frozenset)):
            return list(o)

        # Generic objects -> dict if possible
        if hasattr(o, "__dict__"):
            return vars(o)

        # Last resort: string form
        return str(o)

    # Build json args defensively to avoid collisions
    dumps_kwargs = {
        "ensure_ascii": False,
        "separators": (",", ":"),
        "default": _default,
    }
    # Allow caller overrides without duplicate-key TypeError
    for k, v in json_kwargs.items():
        dumps_kwargs[k] = v

    s = json.dumps(obj, **dumps_kwargs)
    b = s.encode("utf-16le")
    return (b"\xff\xfe" + b) if bom else b


# Example

# payload = {"msg": "Hello", "snowman": "☃", "n": 123}
# data_no_bom = to_utf16le_bytes(payload)            # strict UTF-16LE, no BOM
# data_with_bom = to_utf16le_bytes(payload, bom=True)

# Round-trip example:

# json.loads(data_no_bom.decode("utf-16le"))


def is_numeric(sql_type: str) -> bool:
    # Removed non-existent 'bigmoney'
    return sql_type in [
        'tinyint', 'smallint', 'int', 'bigint',
        'decimal', 'numeric', 'money', 'smallmoney', 'float', 'real'
    ]


def is_char(sql_type: str) -> bool:
    return sql_type in ['char', 'nchar', 'varchar', 'nvarchar', 'sysname', 'text', 'ntext']


def is_date(sql_type: str) -> bool:
    return sql_type in ['date', 'time', 'datetime2', 'datetime', 'smalldatetime']


def is_binary(sql_type: str) -> bool:
    return sql_type in ['binary', 'varbinary']


def _to_mssql_datetimeoffset_literal(val: dt.datetime) -> str:
    if val.tzinfo is None:
        raise ValueError("datetimeoffset requires a timezone-aware datetime")

    offset = val.utcoffset()
    if offset is None:
        raise ValueError("datetimeoffset requires a concrete UTC offset (utcoffset() returned None)")

    # Base date/time with fractional seconds
    dt_str = val.strftime("%Y-%m-%d %H:%M:%S.%f").rstrip("0").rstrip(".")

    # Format offset as ±HH:MM
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    offset_str = f"{sign}{hours:02d}:{minutes:02d}"

    return f"'{dt_str} {offset_str}'"


def _round_datetime_to_datetime_increment(val: dt.datetime) -> dt.datetime:
    """Round to the nearest SQL Server DATETIME increment (1/300 second ≈ 3.333 ms)."""
    # Work in microseconds with Decimal to avoid FP error
    from decimal import Decimal, ROUND_HALF_UP

    midnight = val.replace(hour=0, minute=0, second=0, microsecond=0)
    us_since_midnight = (
            (val.hour * 3_600_000_000)
            + (val.minute * 60_000_000)
            + (val.second * 1_000_000)
            + val.microsecond
    )
    total_seconds = Decimal(us_since_midnight) / Decimal(1_000_000)
    ticks = (total_seconds * Decimal(300)).to_integral_value(rounding=ROUND_HALF_UP)
    new_total_seconds = Decimal(ticks) / Decimal(300)
    new_total_us = int((new_total_seconds * Decimal(1_000_000)).to_integral_value(rounding=ROUND_HALF_UP))

    # Handle potential day rollover (e.g., rounding up to 24:00)
    DAY_US = 86_400_000_000
    days, rem_us = divmod(int(new_total_us), DAY_US)
    return midnight + dt.timedelta(days=int(days), microseconds=int(rem_us))


def to_mssql_literal(val, sql_type: str) -> str:
    """
    Convert a Python value to a SQL Server-compatible string literal
    for the given sql_type (char/nchar/varchar/nvarchar/sysname/text/ntext,
    numeric types, binary/varbinary, date/time/datetime/smalldatetime/datetime2,
    and datetimeoffset).
    """
    sql_type = sql_type.lower().strip()
    # Normalize base type (strip size/precision, e.g., varchar(50) -> varchar)
    base_sql_type = re.split(r"[\s(]", sql_type, 1)[0]

    if val is None:
        return 'NULL'

    # Character types
    if is_char(base_sql_type):
        if base_sql_type in ('nchar', 'nvarchar', 'ntext', 'sysname'):
            return 'N' + quotestr(str(val))
        return quotestr(str(val))

    # BIT type (map truthy to 1, falsy to 0)
    elif base_sql_type == 'bit':
        if isinstance(val, bool):
            return '1' if val else '0'
        if isinstance(val, int):
            return '1' if val != 0 else '0'
        if isinstance(val, str):
            s = val.strip().lower()
            if s in ('1', 'true', 't', 'yes', 'y', 'on'):
                return '1'
            if s in ('0', 'false', 'f', 'no', 'n', 'off', ''):
                return '0'
            raise ValueError("Invalid bit value")
        return '1' if bool(val) else '0'

    # Numeric types
    elif is_numeric(base_sql_type):
        # Normalize bools to 0/1 for numeric targets
        if isinstance(val, bool):
            return '1' if val else '0'

        # Avoid exponent form for decimals/numerics and money types
        if base_sql_type in ('decimal', 'numeric'):
            d = decimal.Decimal(str(val))
            return format(d, 'f')
        if base_sql_type in ('money', 'smallmoney'):
            d = decimal.Decimal(str(val)).quantize(decimal.Decimal('0.0001'))
            return format(d, 'f')
        if base_sql_type in ('float', 'real'):
            f = float(val)
            return format(f, '.17g' if base_sql_type == 'float' else '.7g')
        return str(val)

    elif is_binary(base_sql_type):
        # Binary handling with explicit, predictable semantics
        if isinstance(val, (bytes, bytearray, memoryview)):
            return '0x' + bytes(val).hex().upper()
        elif isinstance(val, str):
            v = val.strip()
            if v.lower().startswith('0x'):
                # Validate hex content (ignore '0x')
                hexpart = v[2:]
                if hexpart == '':
                    return '0x'
                if len(hexpart) % 2 != 0:
                    raise ValueError("Hex literal must have an even number of hex digits for varbinary/binary")
                if all(c in '0123456789abcdefABCDEF' for c in hexpart):
                    return '0x' + hexpart.upper()
                raise ValueError("Invalid hex literal for varbinary/binary")
            # Treat string as text content encoded as UTF-16LE bytes
            return '0x' + v.encode('utf-16le').hex().upper()
        else:
            # Unknown/non-supported type for binary literal → explicit failure
            raise TypeError(
                f"Unsupported value type for {sql_type}: {type(val).__name__}. "
                "Pass bytes/bytearray/memoryview for raw bytes, a '0x..' hex string, or a text string to be UTF-16LE-encoded."
            )

    elif base_sql_type == 'datetimeoffset':
        if not isinstance(val, dt.datetime):
            raise TypeError("datetimeoffset requires a datetime value")
        return _to_mssql_datetimeoffset_literal(val)

    elif is_date(base_sql_type):

        if isinstance(val, dt.date) and not isinstance(val, dt.datetime):
            # Only a date
            if base_sql_type == "date":
                return f"'{val.strftime('%Y-%m-%d')}'"
            else:
                raise ValueError(f"{base_sql_type} requires a datetime, not a date.")

        if isinstance(val, dt.time):
            if val.tzinfo is not None:
                raise ValueError("timezone-aware time is not supported for SQL Server 'time' type")
            if base_sql_type == "time":
                # Trim trailing zeros in fractional seconds
                return "'" + val.strftime("%H:%M:%S.%f").rstrip("0").rstrip(".") + "'"
            else:
                raise ValueError(f"{base_sql_type} requires a datetime, not a time.")

        if isinstance(val, dt.datetime):
            if val.tzinfo is not None:
                raise ValueError("timezone-aware datetime is only supported for 'datetimeoffset'")
            if base_sql_type == "datetime":
                # Round to nearest 1/300 second (≈3.333 ms) and format to milliseconds
                rounded = _round_datetime_to_datetime_increment(val)
                ms = rounded.microsecond // 1000
                return f"'{rounded:%Y-%m-%d %H:%M:%S}.{ms:03d}'"
            elif base_sql_type == "smalldatetime":
                # Round to the nearest minute (≥30s rounds up)
                rounded = val.replace(second=0, microsecond=0)
                if (val.second > 30) or (val.second == 30):
                    rounded = rounded + dt.timedelta(minutes=1)
                return "'" + rounded.strftime("%Y-%m-%d %H:%M") + "'"
            elif base_sql_type == "datetime2":
                # Up to 7 digits of fractional seconds
                return "'" + val.strftime("%Y-%m-%d %H:%M:%S.%f").rstrip("0").rstrip(".") + "'"
            elif base_sql_type == "date":
                return f"'{val.strftime('%Y-%m-%d')}'"
            elif base_sql_type == "time":
                return "'" + val.strftime("%H:%M:%S.%f").rstrip("0").rstrip(".") + "'"
            else:
                raise ValueError(f"Unsupported sql_type: {sql_type}")

        raise TypeError(f"Unsupported value type for {base_sql_type}: {type(val)}")

    else:
        raise ValueError(f"Unsupported sql_type: {sql_type}")


def quotestr(val) -> str:
    if val is None:
        return "NULL"
    return "'" + str(val).replace("'", "''") + "'"
