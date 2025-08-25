import os, requests, smtplib, json
from dotenv import load_dotenv
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone

load_dotenv()

SMTP_HOST = os.getenv("SMTP_HOST","smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT","587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
for k,v in {"SMTP_USER":SMTP_USER,"SMTP_PASS":SMTP_PASS}.items():
    if not v: raise SystemExit(f"Thiếu {k}")

def query_overdue(token, database_id):
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    today_iso = datetime.now(timezone.utc).date().isoformat()
    url = f"https://api.notion.com/v1/databases/{database_id}/query"
    prop_names = [
        "Tình trạng công việc trong tuần",
        "Trạng thái",
        "Status",
        "Tiến độ",
        "Công việc",
    ]
    for prop_name in prop_names:
        for prop_type in ["select", "status"]:
            payload = {
                "filter": {
                    "and": [
                        {"property": prop_name, prop_type: {"equals": "Đang thực hiện"}},
                        {"property": "Deadline dự kiến", "date": {"before": today_iso}}
                    ]
                },
                "page_size": 100
            }
            rows, cursor = [], None
            while True:
                body = dict(payload)
                if cursor: body["start_cursor"] = cursor
                r = requests.post(url, headers=headers, json=body)
                if r.status_code == 401:
                    print(f"401 Unauthorized cho DB {database_id}: token không hợp lệ hoặc không được share quyền.")
                    return []
                if r.status_code == 404:
                    print(f"404 Not Found cho DB {database_id}: database không tồn tại hoặc token không có quyền truy cập.")
                    return []
                if r.status_code == 400:
                    # Nếu lỗi property, thử tên khác
                    err = r.json().get("message", "")
                    if "Could not find property" in err or "property type" in err:
                        break
                    print(f"400 Bad Request cho DB {database_id}: kiểm tra lại database id, token, hoặc cấu trúc query.")
                    print(f"Response: {r.text}")
                    return []
                r.raise_for_status()
                data = r.json()
                rows.extend(data.get("results", []))
                if not data.get("has_more"): break
                cursor = data.get("next_cursor")
            if rows:
                return rows
    print(f"Không tìm thấy property trạng thái phù hợp trong DB {database_id}. Sẽ gửi email với bảng rỗng.")
    return []

def get_prop_text(props, key):
    v = props.get(key, {})
    t = v.get("type")
    if t == "title": return "".join(x.get("plain_text","") for x in v.get("title",[]))
    if t == "rich_text": return "".join(x.get("plain_text","") for x in v.get("rich_text",[]))
    return ""

# Thêm helper để thử nhiều tên property và nhiều kiểu dữ liệu
def find_property_value(props, keys):
    for key in keys:
        p = props.get(key)
        if not p:
            continue
        t = p.get("type")
        # select / status
        if t == "select":
            return p.get("select",{}).get("name","")
        if t == "status":
            return p.get("status",{}).get("name","")
        # title / rich_text
        if t in ("title","rich_text"):
            return get_prop_text(props, key)
        # people
        if t == "people":
            ppl = p.get("people")
            if isinstance(ppl, list) and ppl:
                return ppl[0].get("name") or ppl[0].get("person",{}).get("email","")
        # formula (thường trả về string hoặc select inside)
        if t == "formula":
            f = p.get("formula",{})
            return f.get("string") or (f.get("select") or {}).get("name","") or ""
        # rollup: cố gắng lấy string hoặc phần tử đầu tiên
        if t == "rollup":
            r = p.get("rollup",{})
            if r.get("type") == "array":
                arr = r.get("array",[])
                if arr:
                    first = arr[0]
                    if first.get("type") == "title":
                        return "".join(x.get("plain_text","") for x in first.get("title",[]))
                    if first.get("type") == "rich_text":
                        return "".join(x.get("plain_text","") for x in first.get("rich_text",[]))
            return r.get("string","") or ""
        # fallback: cố gắng dùng một vài trường con phổ biến
        for sub in ("select","status","rich_text","title","formula","rollup"):
            subv = p.get(sub)
            if isinstance(subv, dict):
                return subv.get("name","") or subv.get("string","")
    return ""

# Thêm helper để lấy giá date từ nhiều kiểu property (date, created_time, formula, rollup...)
def find_date_value(props, keys):
    for key in keys:
        p = props.get(key)
        if not p:
            continue
        t = p.get("type")
        # date property
        if t == "date":
            d = p.get("date", {})
            return d.get("start") or d.get("end") or ""
        # created_time / last_edited_time
        if t == "created_time":
            return p.get("created_time","")
        if t == "last_edited_time":
            return p.get("last_edited_time","")
        # formula: may contain date or string
        if t == "formula":
            f = p.get("formula",{})
            if f.get("type") == "date":
                return (f.get("date",{}) or {}).get("start","") or (f.get("date",{}) or {}).get("end","") or ""
            return f.get("string","") or ""
        # rollup: try array/date/string
        if t == "rollup":
            r = p.get("rollup",{})
            if r.get("type") == "array":
                arr = r.get("array",[])
                for item in arr:
                    if item.get("type") == "date":
                        return (item.get("date",{}) or {}).get("start","")
                    if item.get("type") in ("title","rich_text"):
                        # try extract plain text
                        if item.get("type") == "title":
                            return "".join(x.get("plain_text","") for x in item.get("title",[]))
                        if item.get("type") == "rich_text":
                            return "".join(x.get("plain_text","") for x in item.get("rich_text",[]))
            if r.get("type") == "date":
                return (r.get("date",{}) or {}).get("start","") or ""
            return r.get("string","") or ""
        # fallback: sometimes date-like info might sit under 'rich_text' or 'title'
        if t in ("title","rich_text"):
            txt = get_prop_text(props, key)
            if txt:
                return txt
    return ""

def cell_text(prop):
    # Nội dung: ưu tiên "Nội dung công việc", fallback sang "Tên dự án"/"Name"/...
    name = get_prop_text(prop, "Nội dung công việc")
    if not name:
        name = find_property_value(prop, ["Tên dự án", "Name", "Project name", "Project", "Tên"]) or ""
    # PIC: thử people trước, fallback vào select/name
    pic = ""
    ppl_prop = prop.get("PIC") or prop.get("Người phụ trách") or prop.get("Người đảm nhiệm") or prop.get("Pic")
    if ppl_prop:
        if ppl_prop.get("type") == "people":
            ppl = ppl_prop.get("people")
            if isinstance(ppl, list) and ppl:
                pic = ppl[0].get("name") or ppl[0].get("person",{}).get("email","")
        else:
            pic = (ppl_prop.get("select",{}) or {}).get("name","") or get_prop_text(prop, "PIC")
    # Start / Deadline: dùng helper để thử nhiều tên/kiểu
    start_keys = [
        "Start date", "Start", "Start Date", "Ngày bắt đầu", "Ngày bắt đầu dự kiến", "Ngày bắt đầu (Start)"
    ]
    dl_keys = [
        "Deadline dự kiến", "Deadline", "Due date", "Ngày kết thúc", "Ngày dự kiến kết thúc"
    ]
    start = find_date_value(prop, start_keys) or ""
    dl    = find_date_value(prop, dl_keys) or ""
    # Trạng thái: thử nhiều tên property và kiểu khác nhau
    status_keys = [
        "Tình trạng công việc trong tuần",
        "Trạng thái",
        "Status",
        "Tiến độ",
        "Trạng thái công việc",
        "Trạng thái tuần"
    ]
    stt = find_property_value(prop, status_keys) or ""
    return pic, (start[:10] if start else ""), (dl[:10] if dl else ""), stt, name

def build_html(rows):
    if not rows: return "<p>Không có công việc quá hạn 🎉</p>"
    head = """
    <table style="border-collapse:collapse;width:100%">
      <thead><tr>
        <th style="border:1px solid #000;padding:6px">PIC</th>
        <th style="border:1px solid #000;padding:6px">Start</th>
        <th style="border:1px solid #000;padding:6px">Deadline</th>
        <th style="border:1px solid #000;padding:6px">Trạng thái</th>
        <th style="border:1px solid #000;padding:6px">Nội dung công việc</th>
      </tr></thead><tbody>
    """
    body = ""
    for it in rows:
        pic, start, dl, stt, name = cell_text(it["properties"])
        body += f"""<tr>
          <td style="border:1px solid #000;padding:6px">{pic}</td>
          <td style="border:1px solid #000;padding:6px">{start}</td>
          <td style="border:1px solid #000;padding:6px">{dl}</td>
          <td style="border:1px solid #000;padding:6px">{stt}</td>
          <td style="border:1px solid #000;padding:6px">{name}</td>
        </tr>"""
    return head + body + "</tbody></table>"

def send_mail(html, mail_to):
    """
    mail_to: list[str] hoặc comma-separated string.
    Nếu trống, fallback sang env MAIL_TO (có thể là comma-separated).
    """
    # chuẩn hoá mail_to
    if not mail_to:
        env_to = os.getenv("MAIL_TO","").strip()
        if env_to:
            mail_to = [x.strip() for x in env_to.split(",") if x.strip()]
        else:
            print("No recipients provided (neither in JSON nor MAIL_TO env). Skipping send.")
            return False
    if isinstance(mail_to, str):
        mail_to = [x.strip() for x in mail_to.split(",") if x.strip()]
    if not isinstance(mail_to, list):
        mail_to = list(mail_to)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Thông báo trễ hạn (Notion)"
    msg["From"] = SMTP_USER
    msg["To"] = ", ".join(mail_to)
    msg.attach(MIMEText(html, "html", "utf-8"))
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, mail_to, msg.as_string())
        return True
    except Exception as e:
        print(f"Error sending mail to {mail_to}: {e}")
        return False

def get_database_title(token, database_id):
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
    }
    url = f"https://api.notion.com/v1/databases/{database_id}"
    try:
        r = requests.get(url, headers=headers, timeout=10)
    except Exception:
        return ""
    if r.status_code != 200:
        return ""
    data = r.json()
    title_field = data.get("title", []) or []
    if isinstance(title_field, list) and title_field:
        parts = []
        for t in title_field:
            if not isinstance(t, dict):
                continue
            # prefer plain_text, fallback to text.content
            pt = t.get("plain_text") or (t.get("text") or {}).get("content") or ""
            if pt:
                parts.append(pt)
        title = "".join(parts).strip()
        return title
    return ""

if __name__ == "__main__":
    with open("notion_token.json", encoding="utf-8") as f:
        data = json.load(f)

    sent_count = 0
    for token_obj in data.get("notion_tokens", []):
        token = token_obj["token"]
        for db in token_obj.get("databases", []):
            dbid = db["id"]
            # recipients phải lấy từ JSON; nếu không có thì dùng env MAIL_TO như fallback
            recipients = db.get("recipients") or []
            rows = query_overdue(token, dbid)
            if not rows:
                print(f"Không có công việc quá hạn trong DB {dbid}.")
            db_title = get_database_title(token, dbid) or dbid
            html = f"<h3>Database: {db_title}</h3>" + build_html(rows)
            if recipients or os.getenv("MAIL_TO"):
                ok = send_mail(html, recipients)
                if ok:
                    print(f"Sent. Database: {dbid} to {', '.join(recipients) if recipients else os.getenv('MAIL_TO')}")
                    sent_count += 1
                else:
                    print(f"Failed to send. Database: {dbid}")
            else:
                print(f"No recipients for DB {dbid}; skipped sending.")
    print(f"Sent. Databases: {sent_count}")
