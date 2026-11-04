# Kết nối KneeROM qua BLE

```text
IMU cẳng chân → ESP2 DevKit V1 → ESP-NOW kênh 1 → ESP1 S3 + IMU đùi
                                                     ↓ BLE
                                              Laptop chạy KneeROM
                                                     ↓ Wi-Fi Internet
                                              app.kneerom.id.vn
```

Laptop giữ Wi-Fi nhà/trường. Hai ESP không cần mật khẩu mạng và S3 không phát `ACL_Knee` trong bản này.

## Nạp S3 một lần

- Mở `firmware/espnow/esp1_s3_wireless_hub/esp1_s3_wireless_hub.ino`.
- Board: **ESP32S3 Dev Module**. Partition Scheme: **Huge APP (3MB No OTA/1MB SPIFFS)**. Giữ các cấu hình flash/PSRAM phù hợp bo đang dùng.
- Nếu xem Serial qua cổng USB native: **USB CDC On Boot = Enabled**; Serial Monitor 115200.
- I2C đùi: **SDA GPIO4, SCL GPIO5**, VCC/GND giữ theo sơ đồ nguồn đã kiểm tra.
- MAC S3 STA: `84:C7:BB:7C:0A:7C`; ESP2: `68:FE:71:12:5B:68`. ESP2 giữ firmware ESP-NOW hiện tại nếu đã có đúng MAC S3 này.
- Sau nạp, BLE quảng bá tên **KneeROM_7C0A7C**. USB không cần để truyền dữ liệu khi đo, nhưng bo vẫn cần nguồn riêng.

## Mở ứng dụng

1. Bật Bluetooth trên laptop, cấp nguồn cả hai ESP, giữ hai IMU yên để lấy bias.
2. Chạy `D:\bme\mo_web.bat`, hoặc `D:\bme\.venv\Scripts\python.exe D:\bme\app.py`.
3. Web trên máy: `http://127.0.0.1:5001`. Miền công khai dùng được khi máy chạy server và Cloudflare Tunnel có Internet.
4. Đăng nhập bệnh nhân, chọn **Bluetooth BLE**, bấm **Bắt đầu đo**. Server tự tìm đúng S3; không chọn cổng COM cho BLE và không cần ghép đôi trong Settings trước.
5. Lệnh C: đặt mốc duỗi thẳng; F: gập-duỗi chậm 6 giây để hiệu chuẩn trục; L: bật/tắt CSV; R: xóa mốc. Gói tin/định dạng góc giữ như trước.

Ứng dụng đồ thị riêng: `D:\bme\mo_ung_dung.bat`, chọn **BLE · KneeROM_7C0A7C** rồi Kết nối. Lệnh `python knee_longer.py` cũng hoạt động với Python của dự án; bản portable tự chuyển sang Python có Tkinter đã cài trên máy.

Mỗi S3 dùng một trình nhận tại một thời điểm: dừng đo trên web trước khi dùng ứng dụng đồ thị riêng. Máy chạy server phải nằm trong tầm BLE của S3. Điện thoại mở web từ xa xem dữ liệu do laptop này nhận; đây không phải kết nối BLE trực tiếp từ trình duyệt điện thoại.

## Xử lý mất kết nối

- Không tìm thấy S3: kiểm tra đã nạp bản BLE, Bluetooth laptop, nguồn hai bo và tên BLE.
- Đang kết nối nhưng chưa có góc: chờ ESP-NOW/bias, kiểm tra hai IMU rồi thực hiện C/F.
- Backend tự thử lại sau lỗi. Ngắt kết nối xóa thời điểm mẫu cũ; dữ liệu chưa nhận hoặc quá hạn không được lưu như một phép đo hợp lệ.
- BLE chạy nền, dùng hàng đợi giới hạn và ghép dòng nhận bị chia nhỏ. ESP-NOW vẫn kiểm tra MAC, số thứ tự, phiên và thời gian mẫu như trước.
- Bộ lọc góc và lịch đo camera + IMU giữ nguyên. Cần kiểm tra độ trễ/mất gói trên phần cứng sau khi nạp; kiểm thử phần mềm không thay thế thử thực tế.

## Tham khảo kỹ thuật

- [Espressif: RF coexistence ESP32-S3](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-guides/coexist.html)
- [Bleak: BLE client](https://bleak.readthedocs.io/en/latest/api/client.html)

## Hướng lắp đã xác nhận từ ảnh

- Ký hiệu Đ: ESP32-S3 + MPU đùi; ký hiệu C: DevKit V1 + MPU cẳng chân.
- Theo chiều từ đùi xuống cẳng chân, hai MPU cùng mặt chip hướng ra ngoài nhưng ngược đầu 180°.
- `Mounting.h` trong sketch S3 bật `SHANK_Z_180 = true`: đảo X/Y của cả gia tốc và gyro cẳng chân, giữ Z, trước khi lấy bias và chạy Madgwick.
- ESP2 vẫn gửi dữ liệu thô; không đảo trục thêm trên ESP2 để tránh bù hai lần.
- Nếu sau này lắp hai MPU cùng hướng thì đổi `SHANK_Z_180 = false`, nạp lại S3 và hiệu chuẩn lại.
- Sau nạp: giữ yên hai MPU để lấy bias, duỗi thẳng bấm C, bấm F rồi gập-duỗi chậm trong 6 giây. Bấm C lại sẽ xóa trục F cũ.
- Trục F cần chuyển động rõ ít nhất 25° theo một trục; nhiễu nhỏ khi đứng yên không được coi là hiệu chuẩn thành công.
- Đã kiểm tra bằng mô phỏng trường hợp gập 90° và hai cảm biến lắp ngược cùng xoay. Cần kiểm tra góc trên phần cứng sau khi nạp.
