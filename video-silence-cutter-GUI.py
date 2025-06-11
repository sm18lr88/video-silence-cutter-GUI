from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import (
    Any,
    Dict,
    List,
    Literal,
    NamedTuple,
    Optional,
    Protocol,
    TypedDict,
    Union,
)

from typing_extensions import TypeAlias

# ──────────────────────────────────────────────────────────────────────────────
# typing helpers
FilePath: TypeAlias = Union[str, Path]
DBLevel: TypeAlias = float
Duration: TypeAlias = float
TimeStamp: TypeAlias = float
ProgressValue: TypeAlias = float


# message-box wrappers with precise types
def show_info(title: str, message: str) -> str:
    # tkinter stubs mark the return type as “str”, so mirror it
    return messagebox.showinfo(title=title, message=message)  # type: ignore[reportUnknownMemberType]


def show_warning(title: str, message: str) -> str:
    return messagebox.showwarning(title=title, message=message)  # type: ignore[reportUnknownMemberType]


def show_error(title: str, message: str) -> str:
    return messagebox.showerror(title=title, message=message)  # type: ignore[reportUnknownMemberType]


# ──────────────────────────────────────────────────────────────────────────────
class LogLevel(Enum):
    DEBUG = auto()
    INFO = auto()
    WARNING = auto()
    ERROR = auto()


class ProcessingState(Enum):
    IDLE = auto()
    ANALYZING = auto()
    PROCESSING = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()


class VideoInfo(TypedDict):
    duration: Duration
    size: int
    bitrate: int
    video_streams: int
    audio_streams: int
    video_codec: Optional[str]
    audio_codec: Optional[str]
    resolution: Optional[str]
    fps: float


class SystemDependencies(TypedDict):
    ffmpeg: bool
    ffprobe: bool
    cuda: bool


class VideoSegment(NamedTuple):
    start: TimeStamp
    end: TimeStamp


class FilterResult(NamedTuple):
    video_filter: str
    audio_filter: str


# callback protocols
class ProgressCallback(Protocol):
    def __call__(self, value: ProgressValue, message: str = "") -> None: ...


class LogCallback(Protocol):
    def __call__(self, message: str, level: str = "INFO") -> None: ...


# ──────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ProcessingOptions:
    db_threshold: DBLevel = -35.0
    min_silence_duration: Duration = 0.5
    use_cuda: bool = True
    quality_preset: Literal[
        "ultrafast",
        "superfast",
        "veryfast",
        "faster",
        "fast",
        "medium",
        "slow",
        "slower",
        "veryslow",
    ] = "medium"


@dataclass
class ProcessingResult:
    success: bool
    input_duration: Duration
    output_duration: Duration
    removed_duration: Duration
    segments_removed: int
    error_message: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────────
class VideoSilenceCutter:
    """Core engine: no GUI code here."""

    def __init__(
        self,
        progress_callback: Optional[ProgressCallback] = None,
        log_callback: Optional[LogCallback] = None,
    ) -> None:
        self.progress_callback = progress_callback
        self.log_callback = log_callback
        self.ffmpeg_path: str = shutil.which("ffmpeg") or "ffmpeg"
        self.ffprobe_path: str = shutil.which("ffprobe") or "ffprobe"
        self._cancelled: bool = False

    # ── internal helpers ──
    def _log(self, message: str, level: LogLevel = LogLevel.INFO) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] {level.name}: {message}"
        (self.log_callback or print)(line, level.name if self.log_callback else "INFO")

    def _progress(self, value: ProgressValue, message: str = "") -> None:
        if self.progress_callback:
            self.progress_callback(value, message)

    def cancel(self) -> None:
        self._cancelled = True

    # ── dependency check ──
    def check_dependencies(self) -> SystemDependencies:
        def exists(cmd: str) -> bool:
            try:
                subprocess.run([cmd, "-version"], capture_output=True, check=True)
                return True
            except (OSError, subprocess.CalledProcessError):
                return False

        deps: SystemDependencies = {
            "ffmpeg": exists(self.ffmpeg_path),
            "ffprobe": exists(self.ffprobe_path),
            "cuda": False,
        }
        if deps["ffmpeg"]:
            try:
                out = subprocess.run(
                    [self.ffmpeg_path, "-encoders"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                deps["cuda"] = "h264_nvenc" in out.stdout
            except subprocess.CalledProcessError:
                pass
        return deps

    # ── video info ──
    def get_video_info(self, file: FilePath) -> VideoInfo:
        try:
            cp = subprocess.run(
                [
                    self.ffprobe_path,
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    str(file),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            data: Dict[str, Any] = json.loads(cp.stdout)
            fmt = data["format"]
            streams = data["streams"]
            vstreams = [s for s in streams if s["codec_type"] == "video"]
            astreams = [s for s in streams if s["codec_type"] == "audio"]
            fps = 0.0
            if vstreams:
                num, den = (vstreams[0].get("r_frame_rate", "0/1").split("/") + ["1"])[
                    :2
                ]
                try:
                    fps = float(num) / float(den)
                except ZeroDivisionError:
                    pass
            return VideoInfo(
                duration=float(fmt.get("duration", 0)),
                size=int(fmt.get("size", 0)),
                bitrate=int(fmt.get("bit_rate", 0)),
                video_streams=len(vstreams),
                audio_streams=len(astreams),
                video_codec=vstreams[0].get("codec_name") if vstreams else None,
                audio_codec=astreams[0].get("codec_name") if astreams else None,
                resolution=(
                    f"{vstreams[0]['width']}x{vstreams[0]['height']}"
                    if vstreams
                    else None
                ),
                fps=fps,
            )
        except Exception as exc:  # noqa: BLE001
            self._log(f"ffprobe failed: {exc}", LogLevel.ERROR)
            return VideoInfo(
                duration=0.0,
                size=0,
                bitrate=0,
                video_streams=0,
                audio_streams=0,
                video_codec=None,
                audio_codec=None,
                resolution=None,
                fps=0.0,
            )

    # ── silence detection ──
    def find_silences(
        self, file: FilePath, db: DBLevel, min_dur: Duration
    ) -> List[TimeStamp]:
        if self._cancelled:
            return []
        self._log(f"silence: {db} dB / {min_dur}s", LogLevel.DEBUG)
        cp = subprocess.run(
            [
                self.ffmpeg_path,
                "-i",
                str(file),
                "-af",
                f"silencedetect=n={db}dB:d={min_dur}",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
        )
        times: List[TimeStamp] = []
        for line in cp.stderr.splitlines():
            if m := re.search(r"silence_start: ([\d.]+)", line):
                times.append(float(m.group(1)))
            elif m := re.search(r"silence_end: ([\d.]+)", line):
                times.append(float(m.group(1)))
        return times

    # ── segment math ──
    @staticmethod
    def segments_from_silence(
        silences: List[TimeStamp], duration: Duration
    ) -> List[VideoSegment]:
        if not silences:
            return [VideoSegment(0.0, duration)]
        segs: List[VideoSegment] = []
        cur = 0.0
        for i in range(0, len(silences), 2):
            if i + 1 < len(silences):
                start, end = silences[i], silences[i + 1]
                if start > cur:
                    segs.append(VideoSegment(cur, start))
                cur = end
        if cur < duration:
            segs.append(VideoSegment(cur, duration))
        return segs

    @staticmethod
    def make_filters(segs: List[VideoSegment]) -> FilterResult:
        sel = "+".join(f"between(t,{s.start},{s.end})" for s in segs)
        return FilterResult(
            f"select='{sel}',setpts=N/FRAME_RATE/TB",
            f"aselect='{sel}',asetpts=N/SR/TB",
        )

    # ── main process ──
    def process(
        self, inp: FilePath, out: FilePath, opt: ProcessingOptions
    ) -> ProcessingResult:
        self._cancelled = False
        info = self.get_video_info(inp)
        dur = info["duration"]
        if dur == 0:
            return ProcessingResult(False, 0, 0, 0, 0, "Duration unknown")

        sil = self.find_silences(inp, opt.db_threshold, opt.min_silence_duration)
        if self._cancelled:
            return ProcessingResult(False, dur, 0, 0, 0, "Cancelled")

        if not sil:
            shutil.copy2(inp, out)
            return ProcessingResult(True, dur, dur, 0, 0)

        segs = self.segments_from_silence(sil, dur)
        keep = sum(s.end - s.start for s in segs)
        flt = self.make_filters(segs)

        enc = (
            ["-c:v", "h264_nvenc", "-preset", opt.quality_preset]
            if opt.use_cuda
            else ["-c:v", "libx264", "-preset", opt.quality_preset, "-crf", "23"]
        )

        with tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False
        ) as vf, tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as af:
            vf.write(flt.video_filter)
            af.write(flt.audio_filter)
            vf.flush()
            af.flush()

            cmd = [
                self.ffmpeg_path,
                "-y",
                "-i",
                str(inp),
                "-filter_script:v",
                vf.name,
                "-filter_script:a",
                af.name,
                *enc,
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                str(out),
            ]
            cp = subprocess.run(cmd, capture_output=True, text=True)
            os.unlink(vf.name)
            os.unlink(af.name)

        if cp.returncode == 0:
            rem = dur - keep
            return ProcessingResult(True, dur, keep, rem, len(segs) - 1)
        return ProcessingResult(False, 0, 0, 0, 0, cp.stderr[:500])


# ──────────────────────────────────────────────────────────────────────────────
class EnhancedSilenceCutterApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Enhanced Video Silence Cutter")
        self.geometry("820x400")

        self.cut = VideoSilenceCutter(
            progress_callback=self._progress, log_callback=self._log
        )
        self.thread: Optional[threading.Thread] = None

        # tk-variables
        self.var_in = tk.StringVar()
        self.var_out = tk.StringVar()
        self.var_db = tk.StringVar(value="-35")
        self.var_min = tk.StringVar(value="0.5")
        self.var_cuda = tk.BooleanVar(value=True)
        self.var_quality = tk.StringVar(value="medium")
        self.var_prog = tk.StringVar(value="Ready")

        # build UI
        self._ui()
        self._show_deps()

    # ── UI helpers ──
    def _ui(self) -> None:
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=10)
        tab_proc, tab_set, tab_log = ttk.Frame(nb), ttk.Frame(nb), ttk.Frame(nb)
        nb.add(tab_proc, text="Process")
        nb.add(tab_set, text="Settings")
        nb.add(tab_log, text="Log")

        # processing tab
        lf_in = ttk.LabelFrame(tab_proc, text="Input")
        lf_in.pack(fill="x", padx=10, pady=5)
        ttk.Entry(lf_in, textvariable=self.var_in, width=70).pack(
            side="left", expand=True, fill="x", padx=(5, 0), pady=5
        )
        ttk.Button(lf_in, text="Browse", command=self._browse_in).pack(
            side="left", padx=5, pady=5
        )

        lf_out = ttk.LabelFrame(tab_proc, text="Output")
        lf_out.pack(fill="x", padx=10, pady=5)
        ttk.Entry(lf_out, textvariable=self.var_out, width=70).pack(
            side="left", expand=True, fill="x", padx=(5, 0), pady=5
        )
        ttk.Button(lf_out, text="Save As", command=self._browse_out).pack(
            side="left", padx=5, pady=5
        )

        lf_opt = ttk.LabelFrame(tab_proc, text="Options")
        lf_opt.pack(fill="x", padx=10, pady=5)
        ttk.Label(lf_opt, text="Threshold (dB):").grid(row=0, column=0, sticky="w")
        ttk.Entry(lf_opt, textvariable=self.var_db, width=8).grid(row=0, column=1)
        ttk.Label(lf_opt, text="Min silence (s):").grid(row=1, column=0, sticky="w")
        ttk.Entry(lf_opt, textvariable=self.var_min, width=8).grid(row=1, column=1)

        frm_ctl = ttk.Frame(tab_proc)
        frm_ctl.pack(fill="x", padx=10, pady=10)
        ttk.Button(frm_ctl, text="Start", command=self._start).pack(side="left")
        ttk.Button(frm_ctl, text="Stop", command=self._stop).pack(side="left", padx=5)

        lf_prog = ttk.LabelFrame(tab_proc, text="Progress")
        lf_prog.pack(fill="x", padx=10, pady=5)
        ttk.Label(lf_prog, textvariable=self.var_prog).pack(anchor="w", padx=5)
        self.pb = ttk.Progressbar(lf_prog, maximum=100)
        self.pb.pack(fill="x", padx=5, pady=5)

        # settings tab
        ttk.Checkbutton(tab_set, text="Use CUDA", variable=self.var_cuda).pack(
            anchor="w", padx=10, pady=5
        )
        ttk.Label(tab_set, text="Quality:").pack(anchor="w", padx=10)
        ttk.Combobox(
            tab_set,
            textvariable=self.var_quality,
            values=[
                "ultrafast",
                "superfast",
                "veryfast",
                "faster",
                "fast",
                "medium",
                "slow",
                "slower",
                "veryslow",
            ],
            state="readonly",
        ).pack(anchor="w", padx=10, pady=(0, 10))

        lf_sys = ttk.LabelFrame(tab_set, text="System Info")
        lf_sys.pack(fill="both", expand=True, padx=10, pady=5)
        self.txt_sys = tk.Text(lf_sys, height=8, state="disabled", wrap="word")
        self.txt_sys.pack(fill="both", expand=True)

        # log tab
        self.txt_log = scrolledtext.ScrolledText(tab_log, wrap="word", state="disabled")
        self.txt_log.pack(fill="both", expand=True, padx=5, pady=5)

    # ── browse helpers ──
    def _browse_in(self) -> None:
        if fn := filedialog.askopenfilename(
            title="Select video", filetypes=[("All files", "*.*")]
        ):
            self.var_in.set(fn)
            info = self.cut.get_video_info(fn)
            show_info(
                "Video info",
                f"Duration: {info['duration']:.1f}s\nResolution: {info['resolution']}",
            )

    def _browse_out(self) -> None:
        default = Path(self.var_in.get() or "output").with_suffix(".trim.mp4").name
        if fn := filedialog.asksaveasfilename(
            title="Save output", initialfile=default, defaultextension=".mp4"
        ):
            self.var_out.set(fn)

    # ── progress / logging ──
    def _progress(self, value: ProgressValue, message: str = "") -> None:
        self.pb["value"] = value
        if message:
            self.var_prog.set(message)
        self.update_idletasks()

    def _log(self, message: str, level: str = "INFO") -> None:
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", message + "\n")
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    # ── start / stop ──
    def _start(self) -> None:
        if self.thread and self.thread.is_alive():
            show_warning("Busy", "Processing already running.")
            return
        inp, out = self.var_in.get(), self.var_out.get()
        if not inp or not out:
            show_error("Missing", "Select input and output files.")
            return
        try:
            opts = ProcessingOptions(
                db_threshold=float(self.var_db.get()),
                min_silence_duration=float(self.var_min.get()),
                use_cuda=self.var_cuda.get(),
                quality_preset=self.var_quality.get(),  # type: ignore[arg-type]
            )
        except ValueError:
            show_error("Invalid", "Numeric values required.")
            return

        def run() -> None:
            res = self.cut.process(inp, out, opts)
            if res.success:
                show_info(
                    "Done",
                    f"Removed {res.removed_duration:.1f}s "
                    f"({res.removed_duration/res.input_duration*100:.1f}%).",
                )
            else:
                show_error("Failed", res.error_message or "Unknown error")

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def _stop(self) -> None:
        self.cut.cancel()
        self.var_prog.set("Cancelling …")

    # ── system info ──
    def _show_deps(self) -> None:
        dep = self.cut.check_dependencies()
        info = "\n".join(f"{k}: {'✔' if v else '✘'}" for k, v in dep.items())
        self.txt_sys.configure(state="normal")
        self.txt_sys.delete("1.0", "end")
        self.txt_sys.insert("1.0", info)
        self.txt_sys.configure(state="disabled")


# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    EnhancedSilenceCutterApp().mainloop()
