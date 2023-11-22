import subprocess
import tempfile
import os
import tkinter as tk
from tkinter import filedialog, messagebox

def findSilences(filename, dB=-35):
    command = ["ffmpeg", "-i", filename, "-af", "silencedetect=n=" + str(dB) + "dB:d=0.5", "-f", "null", "-"]
    output = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    s = str(output)
    lines = s.split("\\r")
    time_list = []
    for line in lines:
        if "silencedetect" in line:
            words = line.split(" ")
            for i in range(len(words)):
                if "silence_start" in words[i]:
                    time_list.append(float(words[i + 1]))
                if "silence_end" in words[i]:
                    time_list.append(float(words[i + 1]))
    return time_list

def getVideoDuration(filename):
    command = ["ffprobe", "-i", filename, "-v", "quiet", "-show_entries", "format=duration", "-hide_banner", "-of", "default=noprint_wrappers=1:nokey=1"]
    output = subprocess.run(command, stdout=subprocess.PIPE)
    s = str(output.stdout, "UTF-8")
    return float(s)

def getSectionsOfNewVideo(silences, duration):
    return [0.0] + silences + [duration]

def ffmpeg_filter_getSegmentFilter(videoSectionTimings):
    ret = ""
    for i in range(int(len(videoSectionTimings) / 2)):
        start = videoSectionTimings[2 * i]
        end = videoSectionTimings[2 * i + 1]
        ret += "between(t," + str(start) + "," + str(end) + ")+"
    ret = ret[:-1]
    return ret

def getFileContent_videoFilter(videoSectionTimings):
    ret = "select='"
    ret += ffmpeg_filter_getSegmentFilter(videoSectionTimings)
    ret += "', setpts=N/FRAME_RATE/TB"
    return ret

def getFileContent_audioFilter(videoSectionTimings):
    ret = "aselect='"
    ret += ffmpeg_filter_getSegmentFilter(videoSectionTimings)
    ret += "', asetpts=N/SR/TB"
    return ret

def writeFile(filename, content):
    with open(filename, "w") as file:
        file.write(str(content))

def ffmpeg_run(file, videoFilter, audioFilter, outfile):
    # These lines remain the same - they create temporary files for filters
    vFile = tempfile.NamedTemporaryFile(mode="w", encoding="UTF-8", prefix="silence_video")
    aFile = tempfile.NamedTemporaryFile(mode="w", encoding="UTF-8", prefix="silence_audio")
    videoFilter_file = vFile.name
    audioFilter_file = aFile.name
    vFile.close()
    aFile.close()
    writeFile(videoFilter_file, videoFilter)
    writeFile(audioFilter_file, audioFilter)

    # This is the modified FFmpeg command
    # -hwaccel cuda: Enables CUDA hardware acceleration for decoding (if supported by your FFmpeg build and GPU)
    # -c:v h264_nvenc: Use the NVENC H.264 encoder for encoding the output video (requires NVIDIA GPU)
    command = ["ffmpeg", "-hwaccel", "cuda", "-i", file,
               "-filter_script:v", videoFilter_file,
               "-filter_script:a", audioFilter_file,
               "-c:v", "h264_nvenc", outfile]

    # Running the modified command
    subprocess.run(command)

    # Cleaning up the temporary files
    vFile.close()
    aFile.close()

def cut_silences(infile, outfile, dB=-35):
    silences = findSilences(infile, dB)
    duration = getVideoDuration(infile)
    videoSegments = getSectionsOfNewVideo(silences, duration)
    videoFilter = getFileContent_videoFilter(videoSegments)
    audioFilter = getFileContent_audioFilter(videoSegments)
    ffmpeg_run(infile, videoFilter, audioFilter, outfile)

# Tkinter GUI Application
class SilenceCutterApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Silence Cutter")
        self.geometry("400x300")

        self.infile_label = tk.Label(self, text="Input File:")
        self.infile_label.pack()
        self.infile_entry = tk.Entry(self, width=50)
        self.infile_entry.pack()
        self.browse_button = tk.Button(self, text="Browse", command=self.browse_file)
        self.browse_button.pack()

        self.outfile_label = tk.Label(self, text="Output File:")
        self.outfile_label.pack()
        self.outfile_entry = tk.Entry(self, width=50)
        self.outfile_entry.pack()
        self.save_as_button = tk.Button(self, text="Save As", command=self.save_as)
        self.save_as_button.pack()

        self.db_label = tk.Label(self, text="Decibel Level for silence threshold detection (default -30 dB):")
        self.db_label.pack()
        self.db_entry = tk.Entry(self, width=50)
        self.db_entry.insert(0, "-30")
        self.db_entry.pack()

        self.process_button = tk.Button(self, text="Process", command=self.process_file)
        self.process_button.pack()

        self.help_button = tk.Button(self, text="Help", command=self.show_help)
        self.help_button.pack()

    def browse_file(self):
        filename = filedialog.askopenfilename(title="Select a file", filetypes=[("Media files", "*.*")])
        self.infile_entry.delete(0, tk.END)
        self.infile_entry.insert(0, filename)

    def save_as(self):
        outfile = filedialog.asksaveasfilename(title="Save As", filetypes=[("Media files", "*.*")])
        self.outfile_entry.delete(0, tk.END)
        self.outfile_entry.insert(0, outfile)

    def process_file(self):
        infile = self.infile_entry.get()
        outfile = self.outfile_entry.get()
        db = self.db_entry.get()

        if not infile or not outfile:
            messagebox.showerror("Error", "Please specify both input and output files.")
            return

        try:
            db_value = float(db)
        except ValueError:
            messagebox.showerror("Error", "Invalid decibel level for silence threshold.")
            return

        cut_silences(infile, outfile, db_value)
        messagebox.showinfo("Success", "Processing completed.")

    def show_help(self):
        help_text = """
        Usage: Select an input file and an output file. Optionally, set the dB level.
        Default dB level is -30.
        -30 dB: Cuts mouse clicks and movement; cuts are very recognizable.
        -35 dB: Cuts inhaling breath before speaking; cuts are quite recognizable.
        -40 dB: Cuts are almost not recognizable.
        -50 dB: Cuts are almost not recognizable and nothing if there's background noise.
        Dependencies: ffmpeg, ffprobe
        """
        messagebox.showinfo("Help", help_text)

if __name__ == "__main__":
    app = SilenceCutterApp()
    app.mainloop()
