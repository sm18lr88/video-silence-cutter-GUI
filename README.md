# video-silence-cutter-GUI

A tool for removing silences from a video.

GUI adaptation of the Python Video Silence Cutter, originally by [DarkTrick](https://github.com/DarkTrick/python-video-silence-cutter). This tool provides an easy-to-use interface for video silence detection and cutting.

<img src="https://github.com/sm18lr88/video-silence-cutter-GUI/assets/64564447/83a07cde-29ec-4638-9493-923fb8a530ae" width="300">

## Dependencies

- FFmpeg (with NVENC/NVDEC support for GPU acceleration)
- Python
- Tkinter
- NVIDIA CUDA Toolkit (optional, for GPU acceleration)

### Ensuring FFmpeg NVENC/NVDEC Support

To check if your FFmpeg build supports NVENC/NVDEC:

1. Run `ffmpeg -encoders | grep nvenc` in your terminal. This lists NVENC-enabled encoders.
2. If you see entries like `h264_nvenc`, your FFmpeg supports NVENC.

For NVDEC, run `ffmpeg -decoders | grep cuvid`.

If your build does not currently support these, you may need to compile FFmpeg from source with the appropriate flags or download a pre-built version from a trusted source that includes NVENC/NVDEC support.

Note: The application will still run using CPU processing if NVENC/NVDEC support is not available or if a CUDA-enabled GPU is not present. GPU acceleration is an optional enhancement for users with the compatible hardware and software setup.

## License

This project is under Creative Commons 1.0 Universal (CC0 1.0) Public Domain Dedication.
