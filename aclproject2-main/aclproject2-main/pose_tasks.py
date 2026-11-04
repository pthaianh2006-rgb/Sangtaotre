"""Synchronous VIDEO-mode Pose Tasks adapter: one result belongs to its input frame."""
from enum import IntEnum
from pathlib import Path
from types import SimpleNamespace
import time


class PoseLandmark(IntEnum):
    LEFT_HIP=23
    RIGHT_HIP=24
    LEFT_KNEE=25
    RIGHT_KNEE=26
    LEFT_ANKLE=27
    RIGHT_ANKLE=28


class TasksPose:
    def __init__(self, model_path):
        self.model_path=Path(model_path)
        if not self.model_path.is_file():
            raise FileNotFoundError('Missing Pose Landmarker .task model: '+str(self.model_path))
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions
        from mediapipe.tasks.python import vision
        self.mp=mp
        self.vision=vision
        self.options=vision.PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(self.model_path)),
            running_mode=vision.RunningMode.VIDEO,
            num_poses=1,
            min_pose_detection_confidence=0.5,
            min_pose_presence_confidence=0.5,
            min_tracking_confidence=0.5,
            output_segmentation_masks=False,
        )
        self.detector=None
        self.last_timestamp_ms=-1

    def process(self, rgb):
        if self.detector is None:
            self.detector=self.vision.PoseLandmarker.create_from_options(self.options)
        stamp=max(self.last_timestamp_ms+1,time.monotonic_ns()//1_000_000)
        self.last_timestamp_ms=stamp
        result=self.detector.detect_for_video(self.mp.Image(image_format=self.mp.ImageFormat.SRGB,data=rgb),stamp)
        landmarks=result.pose_landmarks[0] if result.pose_landmarks else None
        return SimpleNamespace(pose_landmarks=SimpleNamespace(landmark=landmarks) if landmarks else None,
                               timestamp_ms=stamp)

    def reset(self):
        # Discard tracking history on session/person transfer, even if the camera stays open.
        self.close()

    def close(self):
        if self.detector is not None:
            self.detector.close()
            self.detector=None
