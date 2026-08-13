# MobileCameraAI

Personal Android application for live viewing of Uniview/ONVIF cameras.

## Goal
- Discover camera capabilities through ONVIF.
- Obtain Media Profiles and stream URIs automatically.
- Play live video on Android.
- Support automatic reconnect for temporary network interruptions.
- Personal/private use only.

## Current camera validation
The target Uniview IPC6415SR-X5UPW exposes ONVIF on port 8001 and reports three Media Profiles. ONVIF `GetStreamUri` returns RTSP URIs for the profiles. ONVIF is therefore used for discovery/configuration and the live video transport is provided by the camera's RTSP service.

## Development
The repository is being built incrementally with an engineering test/fix loop.
