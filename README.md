# KneeROM — đo góc đầu gối và theo dõi buổi tập

Ứng dụng Python gồm giao diện đo trực tiếp trên máy tính, web Flask quản lý hồ sơ,
nguồn góc ESP32 qua Wi-Fi/serial và camera MediaPipe. Giao diện dùng tiếng Việt.

## Cài đặt

Môi trường đã kiểm tra: **Python 3.12.10 trên Windows**. Chạy PowerShell tại thư mục dự án:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe web.py --check
```

Nếu đã có `.venv` hoạt động, dùng luôn môi trường đó. `--check` chỉ kiểm tra thư viện,
không mở camera, kết nối ESP32 hoặc sửa cơ sở dữ liệu. Nếu `.venv` báo không tìm thấy
Python nền, cài Python 3.12 rồi tạo lại môi trường ảo.

Các phiên bản trong `requirements.txt` là bộ thư viện đã nạp thành công và vượt qua
`pip check`. Camera hiện dùng API `mp.solutions.pose`, vì vậy giữ MediaPipe 0.10.14;
việc chuyển sang API Tasks cần một đợt chuyển đổi và kiểm chứng góc riêng.
[Xem thông tin phát hành MediaPipe](https://pypi.org/project/mediapipe/0.10.14/).

## Chạy ứng dụng

### Đo bằng giao diện máy tính

```powershell
.\.venv\Scripts\python.exe knee_longer.py
```

Chọn cổng COM, kết nối, hiệu chỉnh theo trạng thái thiết bị rồi bắt đầu ghi.
Tạm dừng/ghi tiếp giữ các mẫu của cùng buổi đo. Chọn buổi mới khi muốn xóa dữ liệu
đang giữ trong bộ nhớ. Xuất Excel cho phép chọn nơi lưu; báo cáo mới được ghi vào
file tạm trước khi thay thế file đích. Đóng ứng dụng sẽ nhắc nếu còn dữ liệu chưa lưu.

### Web

```powershell
.\.venv\Scripts\python.exe web.py
# Không tự mở trình duyệt / chọn cổng khác:
.\.venv\Scripts\python.exe web.py --no-browser --port 5050
```

Mặc định mở tại http://127.0.0.1:5000. Launcher tự chọn Python trong `.venv` và không
phụ thuộc thư mục hiện hành. Các lệnh cũ `python app.py`,
`python tien_ich/app.py` và `aclproject2-main/aclproject2-main/chay_web.bat` vẫn chạy.

Web chỉ khởi động camera/IMU khi truy cập chức năng đo cần thiết. Các trình duyệt
dùng chung một luồng xử lý camera. Một thiết bị phục vụ một bệnh nhân tại một thời
điểm; người khác sẽ nhận thông báo thiết bị đang bận.

### Tài khoản và cấu hình

Tài khoản trong cơ sở dữ liệu cũ được giữ nguyên. Bản mới không tự tạo tài khoản
`admin/admin123`. Để tạo quản trị viên trên cơ sở dữ liệu mới, đặt biến môi trường
trước khi chạy web (mật khẩu tối thiểu 10 ký tự):

```powershell
$env:ACL_ADMIN_USERNAME = "admin"
$adminCredential = Get-Credential -UserName "admin" -Message "Dat mat khau quan tri moi"
$env:ACL_ADMIN_PASSWORD = $adminCredential.GetNetworkCredential().Password
.\.venv\Scripts\python.exe web.py
Remove-Item Env:ACL_ADMIN_PASSWORD
```

Đây là tạo tài khoản lần đầu, không đặt lại mật khẩu của tài khoản đã tồn tại.
Người dùng đăng ký qua web luôn có vai trò bệnh nhân.

Đặt biến môi trường trong cùng cửa sổ PowerShell, ví dụ:

```powershell
$env:ACL_CAMERA_SOURCE = "0"       # Webcam laptop; mặc định dự án là 1
$env:ACL_IMU_MODE = "serial"
$env:ACL_IMU_PORT = "COM6"
.\.venv\Scripts\python.exe web.py
```

| Biến | Mặc định / ý nghĩa |
|---|---|
| `ACL_CAMERA_SOURCE` | `1`; index camera hoặc URL stream |
| `ACL_ENABLE_HARDWARE` | `1`; đặt `0` khi chạy thử web không có thiết bị |
| `ACL_USE_IMU` | `1`; đặt `0` nếu chỉ dùng camera |
| `ACL_IMU_MODE` | `wifi` hoặc `serial` |
| `ACL_IMU_HOST` / `ACL_IMU_TCP_PORT` | `192.168.4.1` / `8080` |
| `ACL_IMU_PORT` / `ACL_IMU_BAUD` | `COM6` / `115200` |
| `ACL_HOST` / `ACL_PORT` | `127.0.0.1` / `5000`; CLI có thể ghi đè |
| `ACL_DEBUG` | Tắt mặc định |
| `ACL_DATABASE` | `aclproject2-main/aclproject2-main/database.db` |
| `ACL_EXPORT_DIR` | Thư mục `exports` cạnh backend; đặt chuỗi rỗng để chỉ tải qua trình duyệt |
| `ACL_SECRET_KEY` | Khóa phiên ngẫu nhiên nếu không đặt; đặt khóa riêng ổn định để giữ phiên qua lần khởi động |
| `ACL_COOKIE_SECURE` | Đặt `1` khi phục vụ qua HTTPS; mặc định tắt cho HTTP localhost |
| `ACL_ADMIN_USERNAME` / `ACL_ADMIN_PASSWORD` | Tạo quản trị viên lần đầu nếu có mật khẩu |

Biến môi trường được đọc khi khởi động. Dùng đường dẫn tuyệt đối cho database và
thư mục xuất tùy chỉnh. Khi không cấu hình `ACL_SECRET_KEY`, khởi động lại web sẽ
yêu cầu đăng nhập lại. Web dùng một tiến trình cho một bộ camera/ESP32; không chạy
nhiều worker độc lập cho cùng thiết bị.

### Tiện ích

```powershell
# Tìm index camera:
.\.venv\Scripts\python.exe tien_ich/tim_camera.py --count 5

# Đo serial không mở GUI; tự dừng sau 30 giây:
.\.venv\Scripts\python.exe tien_ich/knee_longer_cli.py --port COM6 --record --duration 30

# Tạo Word từ Excel đã xuất bởi GUI/CLI:
.\.venv\Scripts\python.exe tien_ich/gen_report.py knee_data.xlsx --output exports/bao_cao.docx
```

CLI mặc định tạo tên Excel theo thời gian. Dùng `--output` để chọn tên và
`--overwrite` nếu chủ động thay thế file đã có. Công cụ Word cũng yêu cầu
`--overwrite` khi thay thế báo cáo. Word nhận sheet `Du lieu` hoặc `Dữ liệu`,
cột B là thời gian theo giây, cột C là góc gập; không dùng file xuất tổng hợp hồ sơ
web làm đầu vào vì cấu trúc khác.

## Kiểm thử

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Các kiểm thử dùng database/file tạm và thiết bị mô phỏng. Nếu có Node.js trên PATH,
bộ kiểm thử cũng kiểm tra cú pháp JavaScript tĩnh và trong các trang được dựng.
Chúng không mở camera/ESP32 thật và không sửa `database.db` đang sử dụng.

Sau kiểm thử tự động, kiểm tra thiết bị thực theo thứ tự:

1. Kết nối đúng camera/COM hoặc mạng Wi-Fi ESP32.
2. Kiểm tra tín hiệu trực tiếp, hiệu chỉnh và thử ngắt/kết nối lại.
3. Ghi một buổi ngắn, tạm dừng/ghi tiếp và mở Excel đã xuất.
4. Đăng nhập web, thử từng nguồn đo rồi lưu và xem lịch sử.
5. Thử trò chơi bằng nguồn thật; chế độ demo không ghi vào hồ sơ.

## Cấu trúc

```text
dtb/
├── app.py                       # Entry point tương thích lệnh cũ
├── web.py                       # Launcher + --check / --no-browser
├── knee_longer.py               # GUI Tkinter đo serial
├── knee_core.py                 # Parser serial + xuất Excel dùng chung
├── requirements.txt
├── tests/                       # Kiểm thử không cần thiết bị thật
├── tien_ich/
│   ├── app.py                   # Launcher tương thích
│   ├── knee_longer_cli.py
│   ├── tim_camera.py
│   └── gen_report.py            # Báo cáo Word từ dữ liệu thực
├── aclproject2-main/aclproject2-main/
│   ├── app.py                   # Backend Flask
│   ├── *.html
│   ├── static/
│   └── database.db              # Dữ liệu đang sử dụng, không ghi vào Git
└── bao_cao_cu/                  # Báo cáo, ảnh và script lịch sử
```

`bao_cao_cu/` được giữ nguyên để đối chiếu. Công cụ báo cáo mới ở `tien_ich/`
không dùng các hình và kết luận cố định của báo cáo lịch sử.

## Các thay đổi chính

- Xuất Excel/Word qua file tạm; dữ liệu cũ còn nguyên nếu lưu thất bại.
- Parser serial giữ phần dòng chưa đủ, giới hạn bộ đệm, bỏ mẫu không hợp lệ.
- GUI giữ dữ liệu khi tạm dừng, xuất nền và cập nhật đường biểu đồ có sẵn.
- Web có CSRF, kiểm tra vai trò/đầu vào, đường dẫn portable và tắt debug mặc định.
- Camera xử lý dùng chung, khởi tạo khi cần; tính góc theo tọa độ có xét tỉ lệ ảnh.
- Giao diện có thông báo lỗi mạng, polling không chồng yêu cầu và hỗ trợ màn hình nhỏ.
- Trò chơi demo không lưu ROM giả vào lịch sử bệnh nhân.
## Kết nối USB và lỗi Access denied

- Chọn cổng có tên USB-SERIAL/CH340/CP210x của cảm biến. Cổng Intel AMT/SOL không phải USB của ESP32. GUI/CLI ưu tiên thiết bị USB trong danh sách nhưng vẫn giữ lựa chọn thủ công khi quét lại.
- Một cổng COM chỉ được một ứng dụng mở tại một thời điểm. Đóng Serial Monitor/Serial Plotter của Arduino hoặc ngắt kết nối từ ứng dụng đang giữ cổng trước khi dùng KneeROM.
- Thông báo lỗi kết nối giữ nguyên chi tiết do Windows/driver trả về. Lỗi quyền sandbox của công cụ phát triển là vấn đề riêng, không dùng làm kết luận lỗi quyền của USB.
- Nếu thiết bị gửi `... send C`, USB đã truyền dữ liệu và firmware đang chờ hiệu chỉnh. GUI sẽ hiển thị trạng thái này; bấm CALIB (C) khi đã chuẩn bị thiết bị để đặt mốc.
- Sau khi cập nhật mã, đóng/mở lại ứng dụng để nạp bản sửa. Lưu dữ liệu đang ghi trước khi đóng.
