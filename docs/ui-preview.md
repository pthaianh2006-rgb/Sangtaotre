# Bản thử giao diện KneeROM

Nhánh `ui/kneerom-responsive` dùng để kiểm tra trước khi hợp nhất vào `main`. Chưa triển khai lên `app.kneerom.id.vn`.

## Chạy bản xem thử

Trong thư mục repo, dùng môi trường Python đã cài dependencies của dự án:

```powershell
python scripts/preview_ui.py
```

Mở http://127.0.0.1:5003. Tài khoản minh họa: `patient_demo` hoặc `doctor_demo`, cùng mật khẩu `preview-only-2026`. Đây là dữ liệu giả, tạo trong thư mục tạm và xóa khi dừng bằng Ctrl+C. Camera, BLE và PostgreSQL thật không được kết nối. Bản thử chỉ lắng nghe trên máy đang chạy.

## Những thay đổi để xem xét

- Trang đăng nhập dùng logo gốc do chủ dự án cung cấp: xanh lá cho bệnh nhân, xanh lam cho bác sĩ. Logo lớn bên trái trên PC và thu nhỏ phía trên form trên điện thoại; hai cổng đăng nhập vẫn riêng biệt.
- Background y tế gốc phủ toàn trang, được làm mờ và giảm độ đậm riêng với nội dung. Biểu tượng nền chuyển màu nhẹ theo tab; vùng giữa được làm dịu để tránh lặp logo. Form và logo phía trước vẫn rõ nét. Hiệu ứng tắt theo tùy chọn giảm chuyển động/giảm trong suốt của thiết bị.
- Bệnh nhân dùng màu xanh ngọc, bác sĩ dùng xanh lam. Font Geist và biểu tượng Phosphor được phục vụ từ repo, kèm giấy phép trong `static/fonts` và `static/icons`.
- Giao diện điều chỉnh cho điện thoại, laptop và PC. Trên điện thoại, điều hướng và nút đo được đặt trong tầm thao tác.
- Camera và IMU đo cùng một lượt: Bắt đầu → Dừng để giữ kết quả → Lưu chung kèm ảnh. Các cài đặt kết nối và hiệu chuẩn nằm trong phần mở rộng.
- Bác sĩ có tìm bệnh nhân, biểu đồ ROM từ dữ liệu thật và cập nhật ảnh/ghi chú. Không có dữ liệu thì hiển thị trạng thái trống.
- Nhánh bao gồm phần xử lý lượt đo chung đang có trên máy chạy web để giữ tương thích với UI. Không thay firmware, cách hiệu chuẩn, cấu trúc database hay quyền tài khoản.

## Ảnh xem trước

Tất cả ảnh dùng dữ liệu minh họa của launcher; camera hiển thị trạng thái chưa mở.

| Màn hình | Desktop | Điện thoại |
| --- | --- | --- |
| Đăng nhập | [Xem ảnh](ui-preview/login-desktop.png) | [Xem ảnh](ui-preview/login-mobile.png) |
| Đăng nhập bác sĩ | [Xem ảnh](ui-preview/login-doctor-desktop.png) | [Xem ảnh](ui-preview/login-doctor-mobile.png) |
| Bệnh nhân | [Xem ảnh](ui-preview/patient-desktop.png) | [Xem ảnh](ui-preview/patient-mobile.png) |
| Bác sĩ | [Xem ảnh](ui-preview/doctor-desktop.png) | [Xem ảnh](ui-preview/doctor-mobile.png) |

## Kiểm tra đã thực hiện

115 kiểm thử Python đã đạt; kiểm thử JavaScript lượt đo chung đã đạt. Edge được kiểm tra ở 320, 390, 1024 và 1440 px: đăng nhập riêng, đăng ký, ẩn/hiện mật khẩu, logo đổi đúng theo cổng đăng nhập, không tràn ngang, đo chung start/stop/save, tìm bệnh nhân, biểu đồ và các trang phụ; không có lỗi JavaScript hay tài nguyên static bị thiếu.

Các trạng thái camera/IMU trong bài kiểm thử trình duyệt được giả lập. Vẫn cần kiểm tra một lượt đo với camera và bộ cảm biến thật trước khi triển khai.

Chạy lại kiểm tra trình duyệt sau khi mở bản thử ở terminal khác:

```powershell
npm install --prefix .test-artifacts/ui-tools --no-save --package-lock=false playwright-core@1.64.1
$env:UI_PLAYWRIGHT_MODULE = (Resolve-Path .test-artifacts/ui-tools/node_modules/playwright-core).Path
node scripts/test_ui.cjs
```

Mặc định dùng Edge trên Windows. Có thể đặt `UI_BROWSER_PATH`, `UI_PREVIEW_URL` và `UI_SCREENSHOT_DIR` để đổi trình duyệt, địa chỉ và nơi lưu ảnh. Chỉ chạy bài kiểm thử này với launcher dữ liệu giả, vì nó thực hiện đăng nhập và một lượt đo giả lập.

## Các file thường chỉnh

- `aclproject2-main/aclproject2-main/home.html`: chữ/form đăng nhập.
- `aclproject2-main/aclproject2-main/dashboard.html`: trang bệnh nhân.
- `aclproject2-main/aclproject2-main/admin_dashboard.html`: trang bác sĩ.
- `aclproject2-main/aclproject2-main/static/portal-ui.css`: màu, bố cục và responsive.
- `static/auth-ui.js` trong cùng thư mục ứng dụng: chuyển tab và chữ động. Trong CSS, `.auth-wallpaper` điều khiển nền y tế, `.auth-panel::before` điều khiển nền trắng phía sau form. Phần `doctor-wallpaper-cutout` trong `home.html` ẩn khung lớn in sẵn trong ảnh background theo đúng tọa độ ảnh, đã kiểm tra thêm ở 1920 px.
