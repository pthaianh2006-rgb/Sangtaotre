# KneeROM

Ứng dụng đo góc gối bằng camera và hai cảm biến MPU6050, lưu phiên đo và theo dõi giữa bệnh nhân với bác sĩ.

## Thành phần

- Web Flask: cổng bệnh nhân, cổng bác sĩ, hồ sơ đo, ảnh tiến triển và nhận xét.
- Camera Camo/OpenCV, MediaPipe Pose Landmarker Tasks API, kiểm tra chất lượng hình và lưu ảnh tại góc lớn nhất.
- Đo camera và IMU cùng lúc: Bắt đầu đo một lần, Dừng đo cả hai nguồn, rồi Lưu kết quả chung.
- Sau khi dừng, kết quả và ảnh được giữ riêng cho phiên đăng nhập trong 10 phút để xem lại và lưu. Không lưu thiếu một nguồn; thử lưu lại không tạo bản ghi trùng.
- Hai kết quả là ROM lớn nhất trong cùng lượt tập, không nhất thiết tại cùng thời điểm. Luồng này không đổi quy ước góc của cảm biến.
- ESP2 DevKit V1 → ESP-NOW → ESP1 ESP32-S3 → BLE → laptop. USB vẫn hỗ trợ chẩn đoán.
- Giao diện Tkinter theo dõi góc và xuất Excel; tiện ích xuất báo cáo Word.
- PostgreSQL qua `ACL_DATABASE_URL`; SQLite dùng cho kiểm thử và chế độ dự phòng được cấu hình rõ ràng.
- LiteRT-LM tùy chọn để tạo bản nháp tóm tắt số liệu cho bác sĩ duyệt.

## Cài đặt và chạy

Môi trường đang dùng: Python 3.12 trên Windows. Tạo môi trường ảo và cài thư viện:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe web.py --check
```

Trên máy đã cấu hình dự án tại `D:\bme`:

```powershell
.\mo_web.bat
.\mo_ung_dung.bat
```

Web tại `http://127.0.0.1:5001`; các lệnh `python app.py` và `python web.py` dùng chung launcher. Trên Windows, launcher mặc định gọi dịch vụ của dự án nếu dịch vụ chưa chạy. Để chạy độc lập trên máy mới, cấu hình biến môi trường và chọn cổng khác:

```powershell
$env:ACL_SECRET_KEY = 'replace-with-a-long-random-secret'
$env:ACL_DATABASE_URL = 'postgresql://kneerom:change-me@127.0.0.1:5432/kneerom'
$env:ACL_ENABLE_HARDWARE = '0'  # Chạy thử giao diện trước khi kết nối phần cứng
.\.venv\Scripts\python.exe web.py --port 5050
```

`.env.example` mô tả các biến; ứng dụng đọc biến môi trường khi khởi động. Không đưa mật khẩu thật vào Git. Các launcher dịch vụ Windows hiện tham chiếu `D:\bme`; cần sửa đường dẫn trước khi dùng trên máy khác.

## Camera, mô hình và BLE

- Cài Camo Studio trên laptop, kết nối camera điện thoại rồi chọn nguồn `Camo`.
- Mô hình camera: `models/pose_landmarker_full.task`.
- Mô hình AI tùy chọn: `models/Qwen3-0.6B.litertlm`, runtime riêng trong `ai_runtime/`.
- Script `scripts/setup_tasks_litert_runtime.py` tải mô hình công khai và cài runtime tùy chọn; mặc định dùng `D:\bme`.
- Xem [hướng dẫn BLE và cách lắp MPU](BLE_KET_NOI.md), [firmware ESP-NOW](firmware/espnow/README.md), [ghi chú Tasks API và AI](aclproject2-main/aclproject2-main/AI_EXPERIMENTS.md).

Máy chạy backend phải ở trong tầm BLE của S3. Người dùng từ xa mở web để xem dữ liệu do máy này nhận. Một bộ cảm biến phục vụ một phiên đo tại một thời điểm. Với hai MPU cùng mặt chip nhưng ngược đầu 180° như cấu hình hiện tại, S3 bù hướng MPU cẳng chân trong `Mounting.h`.

## Kiểm thử

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Các kiểm thử dùng cơ sở dữ liệu tạm và cảm biến giả lập. Kiểm thử GUI cần Python có Tkinter; kiểm thử JavaScript dùng Node.js nếu có. Kiểm thử phần mềm không thay thế kiểm tra góc và đường truyền trên phần cứng thực.

## Phạm vi mã nguồn

Repo gồm backend, giao diện, firmware, launcher, công cụ di chuyển dữ liệu và kiểm thử. Database, ảnh bệnh nhân, báo cáo cá nhân, mật khẩu, chứng chỉ tunnel, log, bản sao lưu, môi trường Python và trọng số mô hình được giữ ngoài Git. Tài khoản và hồ sơ đã có trên máy không bị xóa khi cập nhật source.

## Responsive UI preview

See [desktop/mobile screenshots and preview instructions](docs/ui-preview.md). The login page uses the supplied patient and doctor logos. Review branch `ui/kneerom-responsive` before merging into `main`.
