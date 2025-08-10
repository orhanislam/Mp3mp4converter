from flask import Flask, request, send_file, after_this_request, jsonify
import os
import tempfile
import shutil
import glob
import time
from yt_dlp import YoutubeDL
import base64

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
    return """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>YouTube to MP3/MP4 Converter</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, 'Helvetica Neue', sans-serif; padding: 2rem; max-width: 720px; margin: 0 auto; color: #111; }
    h1 { font-size: 1.6rem; margin-bottom: 1rem; }
    form { display: grid; gap: 0.75rem; }
    .row { display: flex; gap: 0.5rem; }
    input[type=\"text\"] { flex: 1; padding: 0.6rem 0.75rem; border: 1px solid #ccc; border-radius: 8px; font-size: 1rem; }
    .options { display: flex; align-items: center; gap: 1rem; }
    button { background: #111; color: #fff; padding: 0.6rem 1rem; border-radius: 8px; border: none; cursor: pointer; font-size: 1rem; }
    button[disabled] { opacity: 0.6; cursor: not-allowed; }
    .status { min-height: 1.25rem; color: #444; font-size: 0.95rem; }
    .hint { color: #666; font-size: 0.9rem; }
    .card { border: 1px solid #e6e6e6; border-radius: 12px; padding: 1rem; background: #fafafa; }
    details { margin-top: 0.5rem; }
    textarea { width: 100%; min-height: 120px; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace; font-size: 0.9rem; padding: 0.6rem; border: 1px solid #ccc; border-radius: 8px; }
    .subtle { color: #666; font-size: 0.85rem; }
  </style>
</head>
<body>
  <h1>YouTube to MP3/MP4 Converter</h1>

  <div class=\"card\">
    <form id=\"convert-form\">
      <label for=\"url\">YouTube link</label>
      <div class=\"row\">
        <input id=\"url\" name=\"url\" type=\"text\" placeholder=\"https://www.youtube.com/watch?v=...\" required />
      </div>

      <div class=\"options\">
        <label><input type=\"radio\" name=\"format\" value=\"mp3\" checked /> MP3 (highest quality)</label>
        <label><input type=\"radio\" name=\"format\" value=\"mp4\" /> MP4 (highest quality)</label>
      </div>

      <details>
        <summary>Advanced (optional cookies)</summary>
        <div class=\"subtle\" style=\"margin: 0.25rem 0 0.5rem;\">Paste an exported YouTube cookies file (Netscape/yt-dlp format). Used only for this request to bypass sign-in or bot checks.</div>
        <textarea id=\"cookies\" placeholder=\"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t...\"></textarea>
      </details>

      <div class=\"row\">
        <button id=\"download-btn\" type=\"submit\">Convert & Download</button>
      </div>

      <div class=\"status\" id=\"status\"></div>
      <div class=\"hint\">By using this tool, ensure you respect the content owner's rights and platform terms.</div>
    </form>
  </div>

  <script>
    const form = document.getElementById('convert-form');
    const statusEl = document.getElementById('status');
    const button = document.getElementById('download-btn');
    const cookiesEl = document.getElementById('cookies');

    function setBusy(isBusy, message = '') {
      button.disabled = isBusy;
      statusEl.textContent = message;
      button.textContent = isBusy ? 'Processing…' : 'Convert & Download';
    }

    function toBase64Unicode(str) {
      try {
        return btoa(unescape(encodeURIComponent(str)));
      } catch (e) {
        return '';
      }
    }

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const url = document.getElementById('url').value.trim();
      const format = new FormData(form).get('format');

      if (!url) {
        statusEl.textContent = 'Please paste a YouTube link.';
        return;
      }

      setBusy(true, 'Fetching and converting to ' + format.toUpperCase() + '…');

      try {
        const cookiesText = (cookiesEl?.value || '').trim();
        const payload = { url, format };
        if (cookiesText) payload.cookies_b64 = toBase64Unicode(cookiesText);

        const response = await fetch('/api/download', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        if (!response.ok) {
          const err = await response.json().catch(() => ({}));
          throw new Error(err.error || ('Request failed with status ' + response.status));
        }

        const blob = await response.blob();
        let filename = 'download.' + (format === 'mp3' ? 'mp3' : 'mp4');
        const disposition = response.headers.get('Content-Disposition');
        if (disposition) {
          const match = /filename=\"?([^\";]+)\"?/i.exec(disposition);
          if (match) filename = match[1];
        }

        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(link.href);
        setBusy(false, 'Done. Your download should start shortly.');
      } catch (err) {
        console.error(err);
        setBusy(false, err.message || 'Something went wrong.');
      }
    });
  </script>
</body>
</html>"""


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

    # Optional cookies support (from request or environment)
    cookiefile_path = None
    try:
        cookies_b64 = (data.get("cookies_b64") or os.environ.get("YTDLP_COOKIES_B64") or "").strip()
        if cookies_b64:
            cookiefile_path = os.path.join(temp_dir, "cookies.txt")
            with open(cookiefile_path, "wb") as f:
                f.write(base64.b64decode(cookies_b64))
    except Exception:
        cookiefile_path = None

    try:
        base_opts = {
            "outtmpl": os.path.join(temp_dir, "%(title)s.%(ext)s"),
            "quiet": True,
            "noprogress": True,
        }
        if cookiefile_path:
            base_opts["cookiefile"] = cookiefile_path

        if target_format == "mp3":
            ydl_opts = {
                **base_opts,
                "format": "bestaudio/best",
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
                **base_opts,
                "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "merge_output_format": "mp4",
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
        # Provide a clearer message for common YouTube auth requirement
        msg = str(exc)
        if "Sign in to confirm" in msg or "private" in msg.lower():
            msg = (
                "This video requires authentication or cookies. "
                "Paste exported YouTube cookies in Advanced or set YTDLP_COOKIES_B64."
            )
        return jsonify({"error": msg}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)