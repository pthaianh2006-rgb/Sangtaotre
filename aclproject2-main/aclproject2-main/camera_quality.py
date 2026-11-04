"""Conservative frame checks for a visible hip/knee/ankle, not clinical validation."""
import math


class CameraQuality:
    def __init__(self, settle_seconds=0.6):
        self.settle_seconds = settle_seconds
        self.reset()

    def reset(self):
        self.side = None
        self.since = None
        self.previous_knee = None
        self.last_seen = None
        self.status = {"ready": False, "code": "waiting", "message": "Đưa đủ hông, gối và cổ chân vào khung hình."}

    def reject(self, code, message):
        self.since = None
        self.status = {"ready": False, "code": code, "message": message}
        return self.status

    def check(self, points, now, width, height, brightness, sharpness):
        if not points:
            return self.reject("missing", "Chưa thấy chân. Quay ngang người, đưa đủ hông–gối–cổ chân vào hình.")
        if not math.isfinite(brightness) or brightness < 25:
            return self.reject("dark", "Hình quá tối. Tăng ánh sáng chiếu vào chân.")
        if brightness > 245:
            return self.reject("bright", "Hình bị cháy sáng. Tránh nguồn sáng chiếu thẳng vào camera.")
        for p in points:
            if not all(math.isfinite(float(v)) for v in (p.x, p.y, p.visibility)):
                return self.reject("invalid", "Chưa xác định được khớp chân. Đặt lại camera.")
            if not (0.02 <= p.x <= 0.98 and 0.02 <= p.y <= 0.98):
                return self.reject("cropped", "Chân sát mép hoặc ra ngoài hình. Đưa camera ra xa hơn.")
            if p.visibility < 0.6:
                return self.reject("occluded", "Khớp chân chưa rõ. Bỏ vật che hông, gối và cổ chân.")
        if not math.isfinite(sharpness) or sharpness < 12:
            return self.reject("blur", "Hình thiếu nét. Lau ống kính, lấy nét và giữ điện thoại cố định.")
        hip, knee, ankle = points
        lengths = [math.hypot((a.x-b.x)*width, (a.y-b.y)*height) for a,b in ((hip,knee),(knee,ankle))]
        if min(lengths) < min(width, height) * 0.07 or not 0.25 <= lengths[0] / lengths[1] <= 4:
            return self.reject("geometry", "Chân quá nhỏ hoặc bị thu ngắn theo góc nhìn. Quay ngang, đưa camera gần hơn.")
        if self.last_seen is not None and now - self.last_seen > 0.25:
            self.since = None
        self.last_seen = now
        # Reject discontinuous person/landmark jumps; a new location must settle again.
        if self.previous_knee is not None and math.hypot(knee.x-self.previous_knee[0], knee.y-self.previous_knee[1]) > 0.18:
            self.previous_knee = (knee.x, knee.y)
            return self.reject("jump", "Vị trí chân thay đổi đột ngột. Giữ đúng người và chân trong vùng đo.")
        self.previous_knee = (knee.x, knee.y)
        if self.since is None:
            self.since = now
        ready = now - self.since >= self.settle_seconds
        self.status = {"ready": ready, "code": "ready" if ready else "settling",
                       "message": "Hình đủ rõ, đang ghi góc." if ready else "Đã thấy đủ khớp. Giữ khung hình ổn định một chút."}
        return self.status
