from flask import Flask, request, render_template, send_file, after_this_request, jsonify
import os
import tempfile
import shutil
import glob
import time
from yt_dlp import YoutubeDL

app = Flask(__name__)


def _select_latest_file(directory_path: str, extension_glob: str) -> str:
    pattern = os.path.join(directory_path, extension_glob)
    candidates = glob.glob(pattern)
    if not candidates:
        return ""
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates[0]


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/api/download", methods=["POST"]) 
def download():
    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()
    target_format = data.get("format", "mp3").lower()

    if not url:
        return jsonify({"error": "Missing URL"}), 400
    if target_format not in {"mp3", "mp4"}:
        return jsonify({"error": "Invalid format. Use 'mp3' or 'mp4'."}), 400

    temp_dir = tempfile.mkdtemp(prefix="ytdl_")

    # Ensure cleanup of the temp directory after response is sent
    @after_this_request
    def cleanup(response):
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass
        return response

    try:
        if target_format == "mp3":
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": os.path.join(temp_dir, "%(title)s.%(ext)s"),
                "quiet": True,
                "noprogress": True,
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "320",
                    }
                ],
            }
        else:  # mp4
            ydl_opts = {
                "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "outtmpl": os.path.join(temp_dir, "%(title)s.%(ext)s"),
                "merge_output_format": "mp4",
                "quiet": True,
                "noprogress": True,
            }

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            # Prefer gathering final title for download name
            video_title = info.get("title") or "download"

        # Determine resulting file path
        wanted_ext = "mp3" if target_format == "mp3" else "mp4"
        file_path = _select_latest_file(temp_dir, f"*.{wanted_ext}")
        if not file_path:
            # As a fallback, try to infer filename from info
            base_filename = info.get("_filename") or "download"
            base_no_ext = os.path.splitext(os.path.basename(base_filename))[0]
            candidate = os.path.join(temp_dir, f"{base_no_ext}.{wanted_ext}")
            if os.path.exists(candidate):
                file_path = candidate

        if not file_path or not os.path.exists(file_path):
            return jsonify({"error": "Failed to produce output file. Ensure the URL is valid and try again."}), 500

        suggested_name = f"{video_title}.{wanted_ext}"
        # Sanitize name for header safety
        safe_name = "".join(c for c in suggested_name if c not in "\r\n")

        return send_file(
            file_path,
            as_attachment=True,
            download_name=safe_name,
            mimetype="audio/mpeg" if wanted_ext == "mp3" else "video/mp4",
        )

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)