# Notion Overdue Mailer

Script tự động gửi email thông báo công việc quá hạn từ Notion database.

## Tính năng

- ✅ Đọc cấu hình từ JSON
- ✅ Hỗ trợ nhiều database và token
- ✅ Tự động phát hiện schema (title, people, date, status)
- ✅ Lọc công việc quá hạn và đang thực hiện
- ✅ Gửi email HTML với bảng chi tiết
- ✅ Chạy tự động hàng ngày qua GitHub Actions

## Cài đặt

1. **Clone repository:**
   ```bash
   git clone https://github.com/bin115-glitch/Notion.git
   cd Notion
   ```

2. **Cài đặt dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Cấu hình:**
   ```bash
   cp notion_token.json.example notion_token.json
   # Chỉnh sửa notion_token.json với thông tin thật
   
   # Cấu hình danh sách người nhận email
   # Chỉnh sửa email_recipients.json để thêm/sửa/xóa người nhận
   ```

## Cấu hình

### Local Development

Tạo file `notion_token.json`:

```json
{
  "notion_tokens": [
    {
      "token": "ntn_your_notion_token_here",
      "databases": [
        {
          "id": "your_database_id_here"
        }
      ]
    }
  ],
  "smtp": {
    "host": "smtp.gmail.com",
    "port": 587,
    "user": "your-email@gmail.com",
    "pass": "your_app_password_here"
  }
}
```

Tạo file `email_recipients.json` để quản lý danh sách người nhận:

```json
{
  "recipients": [
    "user1@example.com",
    "user2@example.com",
    "user3@example.com"
  ],
  "description": "Danh sách người nhận email cho Notion overdue notifications",
  "last_updated": "2024-01-01"
}
```

**Lưu ý:** Bạn có thể thêm/sửa/xóa người nhận bằng cách chỉnh sửa file `email_recipients.json` này.

### GitHub Actions

Thêm các secrets sau vào GitHub repository:

- `NOTION_TOKEN`: Token Notion của bạn
- `NOTION_DATABASE_ID`: ID database Notion
- `EMAIL_RECIPIENTS`: Danh sách email (JSON array hoặc cách nhau bởi dấu phẩy) - hoặc sử dụng file `email_recipients.json`
- `SMTP_HOST`: smtp.gmail.com
- `SMTP_PORT`: 587
- `SMTP_USER`: Email gửi
- `SMTP_PASS`: App password Gmail

## Sử dụng

### Chạy thủ công:
```bash
python main.py
```

### Chạy tự động:
Script sẽ tự động chạy hàng ngày lúc 4:00 PM (VN) qua GitHub Actions.

## Lấy thông tin cần thiết

### 1. Notion Token
1. Truy cập [Notion Integrations](https://www.notion.so/my-integrations)
2. Tạo integration mới
3. Copy token (bắt đầu với `ntn_`)

### 2. Database ID
1. Mở database trong Notion
2. Copy URL: `https://notion.so/workspace/DATABASE_ID?v=...`
3. Database ID là phần 32 ký tự hex

### 3. Gmail App Password
1. Bật 2-Factor Authentication
2. Tạo App Password: [Google Account Settings](https://myaccount.google.com/)
3. Security → 2-Step Verification → App passwords

## Schema Database

Script tự động phát hiện các trường:
- **Title**: "Nội dung công việc", "Mục tiêu, hiệu quả dự án"
- **PIC**: "PIC", "Người phụ trách", "Owner"
- **Start Date**: "Ngày bắt đầu", "Start Date"
- **Deadline**: "Deadline dự kiến", "Deadline", "Due date"
- **Status**: "Trạng thái cuối cùng", "Tình trạng công việc"

## License

MIT License - xem [LICENSE.txt](LICENSE.txt)