# KneeROM: Pose Tasks API and local LiteRT-LM

## Camera

`pose_tasks.py` uses `PoseLandmarker` with the Full model, `VIDEO` mode and one person.
`detect_for_video` returns the result for the exact input frame. Monotonic millisecond
timestamps are strictly increasing. Transferring/restarting a measurement clears the
native tracker's history. Existing quality checks, filters, paired IMU saves and peak
JPEG capture remain in use. Angles remain calculated in the image plane (2D).

Model: `D:\bme\models\pose_landmarker_full.task` (Google model version 1).
Optional overrides: `ACL_MODEL_DIR`, `ACL_POSE_MODEL`.

## Local AI draft

Open a patient record as a doctor, then select **Tạo tóm tắt dữ liệu**.
`ai_summary.py` aggregates at most the latest 100 measurements. It sends only numeric
statistics to `litert_worker.py` through stdin. The worker runs locally with:

- Runtime: `litert-lm-api==0.19.0` in `D:\bme\ai_runtime` (separate from the web environment).
- Model: `D:\bme\models\Qwen3-0.6B.litertlm`.
- CPU: 2 threads, below-normal Windows process priority, 1 inference job at a time.
- Context: 1536 tokens, output limit: 240 tokens, timeout: 90 seconds.

Each result belongs to the requesting doctor and patient and expires after 5 minutes.
Generated text is rendered as plain text, not HTML. Drafts are not saved or sent to
patients automatically. Numeric values absent from the supplied statistics cause the
draft to be rejected; this check is not a guarantee of factual/clinical correctness.
The doctor must review the draft. No tools, diagnoses or treatment generation are enabled.

Overrides: `ACL_AI_ROOT`, `ACL_AI_RUNTIME`, `ACL_LLM_MODEL`.
Models, package files and compile caches stay on D. The setup script records asset
hashes in `D:\bme\models\tasks-litert-assets.json`.

## Verification and limits

On this machine, the real Pose Tasks Full model detected 33 landmarks in Google's
public test image, rejected a blank image after tracker reset, and produced a valid
peak JPEG through the full processing pipeline. One warmed inference took about 41 ms;
this is an image test, not an end-to-end FPS or accuracy benchmark.

Real LiteRT-LM inference using synthetic ROM statistics completed in about 15 seconds
and generated a short Vietnamese draft. Qwen3 0.6B is a small experimental model;
language quality varies. Testing with actual Camo video and IMU measurements remains
necessary to evaluate tracking, latency and angle agreement in real use.

The existing MediaPipe package stays pinned at 0.10.14, which supports the Tasks API
used here. No application database migration is needed for these two changes.

References:
- https://developers.google.com/edge/mediapipe/solutions/vision/pose_landmarker/python
- https://developers.google.com/edge/litert-lm/python
- https://huggingface.co/litert-community/Qwen3-0.6B
