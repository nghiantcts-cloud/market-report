# Báo cáo thị trường tài chính hàng ngày (chạy cloud, xem trên iPhone)

Mỗi sáng khoảng 7h, GitHub tự chạy tool trên cloud (không cần bật máy), rồi tạo trang web mobile (vàng thế giới, vàng SJC, USD, DXY, dầu WTI/Brent, VN-Index, xu hướng, nhận định, tin kinh tế), lưu lịch sử và gửi tin về Telegram (và/hoặc email) kèm link báo cáo.

Thời gian làm một lần: khoảng 20-30 phút. Nên làm trên máy tính; sau đó dùng hoàn toàn trên iPhone.

## Bước 0. Chuẩn bị
- Tài khoản GitHub (miễn phí): vào https://github.com → Sign up.
- Telegram cài trên iPhone.
- Thư mục này đã tải về máy tính (giữ nguyên các file: market_report.py, requirements.txt, config.env.example, .gitignore, README.md và .github/workflows/daily-report.yml).

## Bước 1. Tạo repo và tải file lên GitHub
1. Đăng nhập GitHub → bấm dấu **+** góc trên bên phải → **New repository**.
2. Repository name: `market-report`. Chọn **Public** (GitHub Pages miễn phí cần repo public). Không tick các ô bên dưới → **Create repository**.
3. Ở trang mới, bấm link **uploading an existing file**. Kéo thả 4 file vào: `market_report.py`, `requirements.txt`, `config.env.example`, `README.md` → kéo xuống bấm **Commit changes**.
4. Tạo file workflow (không nên upload vì tên thư mục bắt đầu bằng dấu chấm): **Add file → Create new file**. Ở ô tên file gõ đúng `.github/workflows/daily-report.yml` (gõ dấu `/` thì GitHub tự tạo thư mục). Mở file `daily-report.yml` trên máy bằng Notepad/TextEdit, copy toàn bộ, dán vào khung nội dung → **Commit changes**.
5. Tương tự, **Add file → Create new file**, tên `.gitignore`, dán nội dung file `.gitignore` → **Commit changes**.

## Bước 2. Tạo bot Telegram
1. Trong Telegram tìm **@BotFather** (có dấu tích xanh) → Start → gửi `/newbot`.
2. Đặt tên hiển thị (ví dụ `Bao cao thi truong`), rồi đặt username phải kết thúc bằng `bot` (ví dụ `baocao_thitruong_bot`).
3. BotFather trả về **token** dạng `123456789:AAH...`. Copy lại và giữ bí mật.
4. Mở bot vừa tạo (bấm link t.me/... trong tin của BotFather), bấm **Start** hoặc nhắn `hi`.
5. Lấy chat id: mở Safari, vào `https://api.telegram.org/bot<TOKEN>/getUpdates` (thay `<TOKEN>`, giữ chữ `bot` phía trước, ví dụ `.../bot123456789:AAH.../getUpdates`). Tìm `"chat":{"id":123456789` và số đó là **chat id**. Nếu kết quả là `"result":[]` thì nhắn thêm một tin cho bot rồi tải lại trang.
6. Kiểm tra nhanh: vào `https://api.telegram.org/bot<TOKEN>/sendMessage?chat_id=<CHAT_ID>&text=test`. Telegram nhận được tin "test" là đúng.

## Bước 3. Lưu token vào GitHub Secrets
1. Trong repo, bấm **Settings** → cột trái **Secrets and variables** → **Actions** → **New repository secret**.
2. Name: `TELEGRAM_BOT_TOKEN`, Secret: token ở bước 2 → **Add secret**.
3. Lặp lại: Name `TELEGRAM_CHAT_ID`, Secret là chat id.
   Tên phải gõ đúng, viết hoa. Secret lưu xong sẽ không xem lại được, chỉ có thể nhập lại.
4. (Tuỳ chọn) Muốn nhận thêm email, tạo thêm các secret: `SMTP_HOST` (Gmail: `smtp.gmail.com`), `SMTP_PORT` (`587`), `SMTP_USER`, `SMTP_PASS`, `MAIL_FROM`, `MAIL_TO`.
   Với Gmail: bật Xác minh 2 bước, rồi tạo "Mật khẩu ứng dụng" (Google Account → Bảo mật) và dùng mật khẩu 16 ký tự đó cho `SMTP_PASS`.

## Bước 4. Chạy thử lần đầu
1. Bấm tab **Actions**. Nếu có nút xanh "I understand my workflows, go ahead and enable them" thì bấm.
2. Cột trái chọn **Daily market report** → bên phải bấm **Run workflow** → **Run workflow** (nút xanh).
3. Đợi 1-2 phút, tải lại trang, bấm vào lần chạy vừa xuất hiện → bấm job **report**.
4. Mở mục **Tạo và gửi báo cáo** để xem log:
   - "Đã tạo báo cáo" và "Đã gửi Telegram" là tốt.
   - Dòng "Nguồn lỗi" cho biết nguồn nào không lấy được.
5. Dấu tích xanh là thành công, dấu X đỏ là lỗi (xem phần Xử lý sự cố).

## Bước 5. Bật GitHub Pages (trang web)
Làm sau bước 4, vì thư mục `docs` chỉ có sau lần chạy đầu tiên.
1. Repo → **Settings** → cột trái **Pages**.
2. Mục Build and deployment: Source = **Deploy from a branch**; Branch = **main**, thư mục = **/docs** → **Save**.
3. Đợi 1-3 phút, tải lại trang Settings → Pages thấy dòng "Your site is live at ...". Link có dạng `https://<tên-github>.github.io/market-report/`.

## Bước 6. Dùng trên iPhone
1. Mở link trang web bằng **Safari** (phải là Safari mới có tính năng này) → nút Chia sẻ (hình vuông có mũi tên lên) → **Thêm vào Màn hình chính** → **Thêm**.
2. Bật thông báo Telegram: Cài đặt iPhone → Thông báo → Telegram → Cho phép. Trong Telegram, đảm bảo cuộc trò chuyện với bot không bị tắt tiếng.
3. Từ hôm sau, mỗi sáng khoảng 7h Telegram sẽ nhận tin tóm tắt kèm link; chạm vào link để mở báo cáo.
4. Muốn cập nhật ngay: Safari → github.com → repo → Actions → Daily market report → Run workflow.

## Xử lý sự cố
- **Telegram không nhận tin**: kiểm tra tên secret (đúng `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`), đã bấm Start với bot chưa, và thử lại bước 2.6.
- **Link báo cáo mở ra 404**: chờ vài phút sau khi bật Pages, kiểm tra Source = main + /docs, và link phải đúng chữ thường.
- **Bước "Lưu lịch sử và trang web" bị lỗi 403**: Settings → Actions → General → Workflow permissions → chọn **Read and write permissions** → Save, rồi chạy lại.
- **Một số mục báo "Nguồn lỗi"** (SJC, VNDirect, Vietcombank, CafeF): có thể do chặn IP nước ngoài hoặc đổi định dạng. Gửi log cho người hỗ trợ để chỉnh, hoặc chạy tool trên máy/VPS tại Việt Nam bằng `python market_report.py --loop`.
- **Không tự chạy mỗi sáng**: lịch của GitHub thường trễ 10-30 phút (workflow đặt 06:30 để đến khoảng 7h). Nếu repo không có hoạt động 60 ngày, GitHub có thể tắt lịch; vào tab Actions bật lại.
- **Muốn repo Private**: xoá dòng `PAGE_URL` trong `.github/workflows/daily-report.yml`. Khi đó Telegram gửi file HTML thay cho link và không có trang web.
