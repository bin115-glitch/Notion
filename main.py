# main.py
# pip install requests python-dotenv

import os, requests, smtplib, sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()  # đọc .env

# ====== ENV ======
NOTION_TOKEN = os.getenv("NOTION_TOKEN")          # bắt buộc
DATABASE_ID  = os.getenv("DATABASE_ID")           # bắt buộc

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")                # bắt buộc (Gmail)
SMTP_PASS = os.getenv("SMTP_PASS")                # bắt buộc (App Password Gmail)
MAIL_TO   = os.getenv("MAIL_TO")                  # bắt buộc, có thể nhiều, cách nhau dấu phẩy

# ====== CHECK ENV ======
missing = [k for k,v in {
    "NOTION_TOKEN": NOTION_TOKEN,
    "DATABASE_ID": DATABASE_ID,
    "SMTP_USER": SMTP_USER,
    "SMTP_PASS": SMTP_PASS,
    "MAIL_TO": MAIL_TO,
}.items() if not v]
if missing:
    print("ERROR: Thiếu biến môi trường:", ", ".join(missing))
    print("Hãy tạo file .env theo mẫu ở hướng dẫn bên dưới.")
    sys.exit(1)

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json"
}

def query_overdue():
    """Lấy các task: Trạng thái = Đang thực hiện AND Deadline dự kiến < hôm nay."""
    today_iso = datetime.now(timezone.utc).date().isoformat()  # YYYY-MM-DD (UTC)
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"

    payload = {
        "filter": {
            "and": [
                {"property": "Tình trạng công việc trong tuần", "select": {"equals": "Đang thực hiện"}},
                {"property": "Deadline dự kiến", "date": {"before": today_iso}}
            ]
        },
        "page_size": 100
    }

    rows = []
    start_cursor = None
    while True:
        if start_cursor:
            payload["start_cursor"] = start_cursor
        r = requests.post(url, headers=HEADERS, json=payload)
        if r.status_code != 200:
            print("Notion API error:", r.status_code, r.text)
            sys.exit(1)
        data = r.json()
        rows.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")
    return rows

def get_pic_name(prop):
    """Hỗ trợ cả kiểu select lẫn people cho cột PIC."""
    # people
    people = prop.get("PIC", {}).get("people")
    if isinstance(people, list) and people:
        p = people[0]
        return p.get("name") or p.get("person", {}).get("email", "")
    # select
    return prop.get("PIC", {}).get("select", {}).get("name", "")

def get_title(prop):
    # tìm cột có type = "title" (chuẩn nhất)
    for k, v in prop.items():
        if v.get("type") == "title":
            blocks = v.get("title", [])
            return "".join(t.get("plain_text", "") for t in blocks)
    # fallback nếu ai đó để tên trong rich_text
    for key in ("Nội dung công việc", "Name", "Tiêu đề", "Task"):
        v = prop.get(key)
        if v and v.get("type") in ("title", "rich_text"):
            blocks = v.get(v["type"], [])
            return "".join(t.get("plain_text", "") for t in blocks)
    return ""

def get_prop_text(props, key):
    v = props.get(key, {})
    t = v.get("type")
    if t == "title":
        return "".join(x.get("plain_text", "") for x in v.get("title", []))
    if t == "rich_text":
        return "".join(x.get("plain_text", "") for x in v.get("rich_text", []))
    return ""

def cell_text(prop):
    # LẤY ĐÚNG CỘT "Nội dung công việc"
    name = get_prop_text(prop, "Nội dung công việc")

    # PIC (people/select)
    people = prop.get("PIC", {}).get("people")
    if isinstance(people, list) and people:
        pic = people[0].get("name") or people[0].get("person", {}).get("email", "")
    else:
        pic = prop.get("PIC", {}).get("select", {}).get("name", "")

    start = prop.get("Start date", {}).get("date", {}).get("start", "")
    dl    = prop.get("Deadline dự kiến", {}).get("date", {}).get("start", "")
    stt   = prop.get("Tình trạng công việc trong tuần", {}).get("select", {}).get("name", "")

    return pic, (start[:10] if start else ""), (dl[:10] if dl else ""), stt, name


def build_html(rows):
    if not rows:
        return "<p>Không có công việc quá hạn 🎉</p>"

    head = """
    <h2>Thông báo công việc quá hạn</h2>
    <table style="border-collapse:collapse;width:100%">
      <thead>
        <tr>
          <th style="border:1px solid #000;padding:6px">PIC</th>
          <th style="border:1px solid #000;padding:6px">Start</th>
          <th style="border:1px solid #000;padding:6px">Deadline</th>
          <th style="border:1px solid #000;padding:6px">Trạng thái</th>
          <th style="border:1px solid #000;padding:6px">Nội dung công việc</th>
        </tr>
      </thead><tbody>
    """
    body = ""
    for it in rows:
        pic, start, dl, stt, name = cell_text(it["properties"])
        body += f"""
        <tr>
          <td style="border:1px solid #000;padding:6px">{pic}</td>
          <td style="border:1px solid #000;padding:6px">{start}</td>
          <td style="border:1px solid #000;padding:6px">{dl}</td>
          <td style="border:1px solid #000;padding:6px">{stt}</td>
          <td style="border:1px solid #000;padding:6px">{name}</td>

        </tr>"""
    tail = "</tbody></table>"
    return head + body + tail

def send_mail(html):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Thông báo trễ hạn (Notion)"
    msg["From"] = SMTP_USER
    msg["To"] = MAIL_TO
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)          # Gmail App Password
        s.sendmail(SMTP_USER, [m.strip() for m in MAIL_TO.split(",")], msg.as_string())

if __name__ == "__main__":
    rows = query_overdue()
    html = build_html(rows)
    send_mail(html)
    print(f"Sent. Items: {len(rows)}")
