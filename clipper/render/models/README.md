# Vendored detector model

This directory holds the face detector used by the subject-focused 9:16 crop
(ADR-001, `docs/design/subject-crop-spec.md`). The model is vendored so
detection runs fully local, with no network call at runtime.

## `face_detection_yunet_2023mar.onnx`

- Model: YuNet face detector (2023 March release).
- Source: [opencv/opencv_zoo](https://github.com/opencv/opencv_zoo), path
  `models/face_detection_yunet/face_detection_yunet_2023mar.onnx`.
- Fetched via the Git LFS media URL:
  `https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx`
- Size: 232589 bytes.
- sha256: `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`
- License: MIT (`./LICENSE`, Copyright (c) 2020 Shiqi Yu). The upstream YuNet
  model is MIT-licensed, not Apache-2.0.

## Verify the checksum

Run this from the repository root:

    shasum -a 256 render/models/face_detection_yunet_2023mar.onnx

The output must match the sha256 above.
