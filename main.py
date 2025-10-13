# -*- coding: utf-8 -*-
"""
main.py — Notion overdue mailer (Environment Variables)

- Đọc cấu hình từ environment variables
- Hỗ trợ fallback về JSON config nếu cần
- Bảo mật thông tin nhạy cảm
"""
import os
import re
import json
import smtplib
from typing import Optional, Tuple, Dict, Any, List
from datetime import datetime, timezone
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Environment variables với fallback
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
# Load email recipients from JSON file
def load_email_recipients():
    """Load email recipients from JSON file with fallback to environment variable"""
    recipients_file = os.getenv("EMAIL_RECIPIENTS_FILE", "email_recipients.json")
    
    try:
        # Try to load from JSON file first
        if os.path.exists(recipients_file):
            with open(recipients_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get("recipients", [])
    except (json.JSONDecodeError, FileNotFoundError, KeyError) as e:
        print(f"Warning: Could not load recipients from {recipients_file}: {e}")
    
    # Fallback to environment variable
    _email_recipients_raw = os.getenv("EMAIL_RECIPIENTS", "")
    if _email_recipients_raw:
        try:
            # Try to parse as JSON array first
            recipients = json.loads(_email_recipients_raw)
            if isinstance(recipients, list):
                return recipients
        except (json.JSONDecodeError, ValueError):
            pass
        # Fallback to comma-separated string
        return [email.strip() for email in _email_recipients_raw.split(",") if email.strip()]
    
    return []

EMAIL_RECIPIENTS = load_email_recipients()
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")

# Fallback config path (nếu cần)
CONFIG_PATH = os.getenv("NOTION_CONFIG", "notion_token.json")
DEFAULT_STATUS_EQUALS = "Đang thực hiện"

TITLE_CANDS    = ["Nội dung công việc", "Mục tiêu, hiệu quả dự án","Chi tiết công việc"]
PIC_CANDS      = ["PIC", "Người phụ trách", "Owner", "Assignee"]
START_CANDS    = ["Ngày bắt đầu", "Start Date", "Start date"]
DEADLINE_CANDS = ["Deadline dự kiến", "Deadline", "Due date", "Due", "Ngày đến hạn"]
STATUS_CANDS   = ["Trạng thái cuối cùng", "Tình trạng công việc trong tuần", "Tình trạng", "Status"]


def _headers(token: str) -> Dict[str,str]:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }

def _extract_uuid(s: str) -> Optional[str]:
    if not s:
        return None
    s = s.strip()
    if s.startswith("http"):
        s = s.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    m = re.search(r"([0-9a-fA-F]{32}|[0-9a-fA-F-]{36})$", s)
    return m.group(1).replace("-", "").lower() if m else None

def resolve_db_ids(token: str, raw: str, max_depth: int = 3) -> List[str]:
    """Return list of database ids (32-hex, no dashes). Accepts page/db URL or id."""
    uid = _extract_uuid(raw)
    if not uid:
        raise ValueError(f"Không trích được UUID từ: {raw}")
    h = _headers(token)
    r = requests.get(f"https://api.notion.com/v1/databases/{uid}", headers=h)
    if r.status_code == 200:
        return [uid]
    r = requests.get(f"https://api.notion.com/v1/pages/{uid}", headers=h)
    if r.status_code != 200:
        raise ValueError("Không phải database/page hoặc token không có quyền.")
    def walk(block_id: str, depth: int) -> List[str]:
        if depth < 0:
            return []
        out: List[str] = []
        cursor = None
        while True:
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            rr = requests.get(f"https://api.notion.com/v1/blocks/{block_id}/children", headers=h, params=params)
            rr.raise_for_status()
            data = rr.json()
            for b in data.get("results", []):
                t = b.get("type")
                if t == "child_database":
                    out.append(b["id"].replace("-", "").lower())
                elif t == "link_to_database":
                    did = b[t].get("database_id")
                    if did:
                        out.append(did.replace("-", "").lower())
                if b.get("has_children"):
                    out += walk(b["id"], depth - 1)
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        # unique
        seen = {}
        return [seen.setdefault(x, x) for x in out if x not in seen]
    ids = walk(uid, max_depth)
    if not ids:
        raise ValueError("Page không chứa database (hoặc chưa Add connection).")
    return ids

def _get_db_props(token: str, dbid: str) -> Dict[str,Any]:
    r = requests.get(f"https://api.notion.com/v1/databases/{dbid}", headers=_headers(token))
    r.raise_for_status()
    return r.json().get("properties", {})

def _db_title(token: str, dbid: str) -> str:
    try:
        r = requests.get(f"https://api.notion.com/v1/databases/{dbid}", headers=_headers(token))
        if r.status_code != 200:
            return dbid
        obj = r.json()
        title = "".join(t.get("plain_text", "") for t in (obj.get("title") or []))
        return title or dbid
    except Exception:
        return dbid

def _normalize(s: str) -> str:
    return " ".join((s or "").split()).lower()

def _find_prop_by_name(props: Dict[str,Any], target_name: str, want_types=("status","select","date")) -> Optional[Dict[str,str]]:
    norm = _normalize(target_name)
    for name, meta in props.items():
        if _normalize(name) == norm and meta.get("type") in want_types:
            return {"name": name, "id": meta["id"], "type": meta["type"]}
    return None

def _pick_deadline_col(props: Dict[str,Any]) -> Optional[Dict[str,str]]:
    for name in DEADLINE_CANDS:
        meta = props.get(name)
        if meta and meta.get("type") == "date":
            return {"name": name, "id": meta["id"], "type": "date"}
    for name, meta in props.items():
        if meta.get("type") == "date":
            return {"name": name, "id": meta["id"], "type": "date"}
    return None

# ---- value extractors (robust to naming) ----
def _get_text(props: Dict[str,Any], key: str) -> str:
    v = props.get(key, {})
    t = v.get("type")
    if t == "title":
        return "".join(x.get("plain_text", "") for x in v.get("title", []))
    if t == "rich_text":
        return "".join(x.get("plain_text", "") for x in v.get("rich_text", []))
    return ""

def _pick_first(props: Dict[str,Any], names: List[str], want_type: Optional[str] = None):
    for k in names:
        v = props.get(k)
        if not v:
            continue
        if want_type is None or v.get("type") == want_type:
            return k, v
    return None, None

def _any_status(props: Dict[str,Any]) -> str:
    name, v = _pick_first(props, STATUS_CANDS)
    if v:
        t = v.get("type")
        if t in ("status", "select"):
            return (v.get(t) or {}).get("name", "")
    for vv in props.values():
        t = vv.get("type")
        if t in ("status","select"):
            return (vv.get(t) or {}).get("name", "")
    return ""

def _any_people(props: Dict[str,Any]) -> str:
    name, v = _pick_first(props, PIC_CANDS)
    if v and v.get("type") == "people":
        ppl = v.get("people") or []
        if ppl:
            return ppl[0].get("name") or ppl[0].get("person", {}).get("email", "")
    if v and v.get("type") == "select":
        return (v.get("select") or {}).get("name", "")
    for vv in props.values():
        if vv.get("type") == "people":
            ppl = vv.get("people") or []
            if ppl:
                return ppl[0].get("name") or ppl[0].get("person", {}).get("email", "")
    return ""

def _any_date(props: Dict[str,Any], names: List[str]) -> str:
    k, v = _pick_first(props, names, want_type="date")
    if v:
        s = (v.get("date") or {}).get("start", "")
        return s[:10] if s else ""
    for vv in props.values():
        if vv.get("type") == "date":
            s = (vv.get("date") or {}).get("start", "")
            return s[:10] if s else ""
    return ""

def _any_title(props: Dict[str,Any]) -> str:
    for k, v in props.items():
        if v.get("type") == "title":
            return "".join(x.get("plain_text", "") for x in v.get("title", []))
    for k in TITLE_CANDS:
        t = _get_text(props, k)
        if t:
            return t
    return ""

def cell_text(props: Dict[str,Any]) -> tuple[str,str,str,str,str]:
    name  = _any_title(props)
    pic   = _any_people(props)
    start = _any_date(props, START_CANDS)
    dl    = _any_date(props, DEADLINE_CANDS)
    stt   = _any_status(props)
    return pic, start, dl, stt, name

def query_overdue(token: str, database_id: str, schema: Optional[Dict[str,str]] = None, status_equals: Optional[str] = DEFAULT_STATUS_EQUALS) -> List[Dict[str,Any]]:
    props = _get_db_props(token, database_id)
    # Deadline
    if schema and schema.get("deadline") in props and props[schema["deadline"]]["type"] == "date":
        meta = props[schema["deadline"]]
        deadline_prop = {"name": schema["deadline"], "id": meta["id"], "type": "date"}
    else:
        deadline_prop = _pick_deadline_col(props)
    # Status
    status_prop = None
    if schema and schema.get("status"):
        status_prop = _find_prop_by_name(props, schema["status"], want_types=("status","select"))
    if not status_prop:
        for nm in STATUS_CANDS:
            status_prop = _find_prop_by_name(props, nm, want_types=("status","select"))
            if status_prop:
                break
        if not status_prop:
            for name, meta in props.items():
                if meta.get("type") in ("status","select"):
                    status_prop = {"name": name, "id": meta["id"], "type": meta["type"]}
                    break
    today_iso = datetime.now(timezone.utc).date().isoformat()
    url = f"https://api.notion.com/v1/databases/{database_id}/query"
    filters = []
    if deadline_prop:
        filters.append({"property": deadline_prop["id"], "date": {"before": today_iso}})
    payload = {"page_size": 100}
    if status_equals and status_prop:
        operator = status_prop["type"]
        filters.append({"property": status_prop["id"], operator: {"equals": status_equals}})
    if filters:
        payload["filter"] = {"and": filters}
    rows: List[Dict[str,Any]] = []
    cursor = None
    while True:
        body = dict(payload)
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(url, headers=_headers(token), json=body)
        r.raise_for_status()
        data = r.json()
        rows.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    if status_equals and not status_prop:
        kept = []
        for it in rows:
            st = _any_status(it.get("properties", {}))
            if _normalize(st) == _normalize(status_equals):
                kept.append(it)
        rows = kept
    return rows

# Thêm: truy vấn theo trạng thái (không lọc theo deadline)
def query_status(token: str, database_id: str, status_equals: Optional[str] = DEFAULT_STATUS_EQUALS) -> List[Dict[str,Any]]:
    props = _get_db_props(token, database_id)
    # tìm property status giống query_overdue
    status_prop = None
    for nm in STATUS_CANDS:
        status_prop = _find_prop_by_name(props, nm, want_types=("status","select"))
        if status_prop:
            break
    if not status_prop:
        for name, meta in props.items():
            if meta.get("type") in ("status","select"):
                status_prop = {"name": name, "id": meta["id"], "type": meta["type"]}
                break
    url = f"https://api.notion.com/v1/databases/{database_id}/query"
    payload = {"page_size": 100}
    if status_equals and status_prop:
        operator = status_prop["type"]
        payload["filter"] = {"property": status_prop["id"], operator: {"equals": status_equals}}
    rows: List[Dict[str,Any]] = []
    cursor = None
    while True:
        body = dict(payload)
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(url, headers=_headers(token), json=body)
        r.raise_for_status()
        data = r.json()
        rows.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    # nếu không có status_prop nhưng có status_equals -> lọc client-side
    if status_equals and not status_prop:
        kept = []
        for it in rows:
            st = _any_status(it.get("properties", {}))
            if _normalize(st) == _normalize(status_equals):
                kept.append(it)
        rows = kept
    return rows

def build_html(rows: List[Dict[str,Any]]) -> str:
    if not rows:
        return "<p>Không có công việc quá hạn 🎉</p>"
    head = (
        "<table style=\"border-collapse:collapse;width:100%\">"
        "<thead><tr>"
        "<th style='border:1px solid #000;padding:6px'>PIC</th>"
        "<th style='border:1px solid #000;padding:6px'>Start</th>"
        "<th style='border:1px solid #000;padding:6px'>Deadline</th>"
        "<th style='border:1px solid #000;padding:6px'>Trạng thái</th>"
        "<th style='border:1px solid #000;padding:6px'>Nội dung công việc</th>"
        "</tr></thead><tbody>"
    )
    body = []
    for it in rows:
        pic, start, dl, stt, name = cell_text(it.get("properties", {}))
        body.append(
            "<tr>"
            f"<td style='border:1px solid #000;padding:6px'>{pic}</td>"
            f"<td style='border:1px solid #000;padding:6px'>{start}</td>"
            f"<td style='border:1px solid #000;padding:6px'>{dl}</td>"
            f"<td style='border:1px solid #000;padding:6px'>{stt}</td>"
            f"<td style='border:1px solid #000;padding:6px'>{name}</td>"
            "</tr>"
        )
    return head + "".join(body) + "</tbody></table>"

def send_mail(to_list: List[str], html: str, smtp_cfg: Dict[str,Any]):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Thông báo trễ hạn (Notion)"
    msg["From"] = smtp_cfg["user"]
    msg["To"] = ", ".join(to_list)
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP(smtp_cfg.get("host","smtp.gmail.com"), int(smtp_cfg.get("port",587))) as s:
        s.starttls()
        s.login(smtp_cfg["user"], smtp_cfg["pass"])
        s.sendmail(smtp_cfg["user"], to_list, msg.as_string())

def load_config_from_env() -> Dict[str, Any]:
    """Load configuration from environment variables"""
    missing = []
    if not NOTION_TOKEN:
        missing.append("NOTION_TOKEN")
    if not NOTION_DATABASE_ID:
        missing.append("NOTION_DATABASE_ID")
    if not SMTP_USER:
        missing.append("SMTP_USER")
    if not SMTP_PASS:
        missing.append("SMTP_PASS")
    
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
    
    return {
        "notion_tokens": [
            {
                "token": NOTION_TOKEN,
                "databases": [
                    {
                        "id": NOTION_DATABASE_ID,
                        "recipients": EMAIL_RECIPIENTS
                    }
                ]
            }
        ],
        "smtp": {
            "host": SMTP_HOST,
            "port": SMTP_PORT,
            "user": SMTP_USER,
            "pass": SMTP_PASS
        }
    }

def load_config() -> Dict[str, Any]:
    """Load config from environment variables first, then fallback to JSON"""
    try:
        print("Loading configuration from environment variables...")
        return load_config_from_env()
    except ValueError as e:
        print(f"Environment config error: {e}")
        # Fallback to JSON config
        if os.path.exists(CONFIG_PATH):
            print(f"Falling back to JSON config: {CONFIG_PATH}")
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        else:
            raise ValueError("No configuration found in environment variables or JSON file")

def main():
    try:
        config = load_config()
        token_entries = config["notion_tokens"]
        smtp_cfg = config["smtp"]
    except Exception as e:
        print(f"Error loading config: {e}")
        return
    
    sent = 0
    for idx, t in enumerate(token_entries, 1):
        token = t["token"]
        for db in t.get("databases", []):
            raw = (db.get("id") or "").strip()
            recipients = [x.strip() for x in db.get("recipients", []) if x.strip()]
            if not raw or not recipients:
                continue
            schema = db.get("schema") or None
            status_equals = db.get("status_equals", DEFAULT_STATUS_EQUALS)
            try:
                dbids = resolve_db_ids(token, raw)
            except Exception as e:
                print(f"Skip '{raw}': {e}")
                continue
            for dbid in dbids:
                try:
                    rows = query_overdue(token, dbid, schema=schema, status_equals=status_equals)
                except requests.HTTPError as e:
                    print(f"HTTPError khi query DB {dbid}: {e}")
                    continue
                # Lấy thêm các công việc đang thực hiện (không quan tâm deadline)
                try:
                    in_progress_rows = query_status(token, dbid, status_equals=status_equals)
                except requests.HTTPError as e:
                    print(f"HTTPError khi query status DB {dbid}: {e}")
                    in_progress_rows = []
                title = _db_title(token, dbid)
                # Gộp 2 bảng: quá hạn và đang thực hiện
                html = f"<h3>Database: {title}</h3>"
                html += "<h4>Công việc quá hạn</h4>" + build_html(rows)
                html += "<br><h4>Công việc đang thực hiện</h4>" + build_html(in_progress_rows) + "<br>"
                try:
                    send_mail(recipients, html, smtp_cfg)
                    print(f"Sent. Database: {title} → {', '.join(recipients)}")
                    sent += 1
                except Exception as e:
                    print(f"Gửi mail lỗi cho DB {title}: {e}")
    print(f"Done. Emails sent: {sent}")

if __name__ == "__main__":
    main()