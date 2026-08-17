import sys
import os
import queue
import re
import glob

# ==========================================
# 【終極修復】修正 Bad file descriptor 崩潰問題
# ==========================================
system_log_queue = queue.Queue()

class GUIWriter:
    def __init__(self):
        # 【關鍵】開啟系統底層的空裝置 (devnull)，取得真實合法的檔案描述符
        self.null_file = open(os.devnull, 'w')

    def write(self, data):
        # 攔截所有 print 和系統報錯，丟進佇列中
        if data and data.strip():
            system_log_queue.put(data.strip())

    def flush(self):
        pass

    def isatty(self):
        return False

    def fileno(self):
        # 【關鍵】回傳真實合法的空裝置描述符，徹底騙過 Flask 的 click 模組！
        return self.null_file.fileno()

if getattr(sys, 'frozen', False):
    # 打包成 EXE 後，強制把所有輸出導向我們的攔截器
    sys_writer = GUIWriter()
    sys.stdout = sys_writer
    sys.stderr = sys_writer

# ==========================================
# 正常 Import 區
# ==========================================
import tkinter as tk
from tkinter import messagebox
# ... 下面的 import 保留原樣 ...
import subprocess
import shutil
import threading
import socket
import json
import time
import webbrowser
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from flask import Flask, render_template, request, send_from_directory
from flask_socketio import SocketIO, emit
import multiprocessing

# ==========================================
# 設定區
# ==========================================
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable) 
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
FFMPEG_DIR = os.path.join(BASE_DIR, "ffmpeg", "bin")
YT_DLP_PATH = os.path.join(BASE_DIR, "yt-dlp.exe")

def get_ffmpeg_executable():
    local_ffmpeg = os.path.join(FFMPEG_DIR, "ffmpeg.exe")
    if os.path.exists(local_ffmpeg):
        return local_ffmpeg

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg

    winget_ffmpeg = os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "Microsoft",
        "WinGet",
        "Packages",
        "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe",
        "ffmpeg-9.0-full_build",
        "bin",
        "ffmpeg.exe",
    )
    if os.path.exists(winget_ffmpeg):
        return winget_ffmpeg

    return None

def get_deno_executable():
    system_deno = shutil.which("deno")
    if system_deno:
        return system_deno

    winget_deno = os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "Microsoft",
        "WinGet",
        "Packages",
        "DenoLand.Deno_Microsoft.Winget.Source_8wekyb3d8bbwe",
        "deno.exe",
    )
    if os.path.exists(winget_deno):
        return winget_deno

    return None

def get_ytdlp_command():
    if os.path.exists(YT_DLP_PATH):
        return [YT_DLP_PATH]
    return [sys.executable, "-m", "yt_dlp"]

RESOLVED_FFMPEG = get_ffmpeg_executable()
if RESOLVED_FFMPEG:
    resolved_ffmpeg_dir = os.path.dirname(RESOLVED_FFMPEG)
    if resolved_ffmpeg_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] += os.pathsep + resolved_ffmpeg_dir

if os.path.exists(FFMPEG_DIR) and FFMPEG_DIR not in os.environ.get("PATH", ""):
    os.environ["PATH"] += os.pathsep + FFMPEG_DIR
os.environ["PATH"] += os.pathsep + BASE_DIR

SONGS_DIR = os.path.join(BASE_DIR, "ktv_songs")
TEMP_BASE_DIR = os.path.join(BASE_DIR, "temp_processing") 

if not os.path.exists(SONGS_DIR): os.makedirs(SONGS_DIR)
if not os.path.exists(TEMP_BASE_DIR): os.makedirs(TEMP_BASE_DIR)

# ==========================================
# Flask + SocketIO 伺服器
# ==========================================
app = Flask(__name__, template_folder=TEMPLATES_DIR)
app.config['SECRET_KEY'] = 'ktv_secret'
socketio = SocketIO(app, cors_allowed_origins="*")

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

LOCAL_IP = get_local_ip()
PORT = 5000

def broadcast_log(msg):
    # 用 print 就會自動被我們的 GUIWriter 抓走並顯示在介面上
    print(msg)
    socketio.emit('admin_log', {'msg': msg})


def broadcast_song_list():
    songs = sorted([f for f in os.listdir(SONGS_DIR) if f.lower().endswith('.mp4')])
    socketio.emit('refresh_list')
    socketio.emit('update_list', songs)
    return songs


def remove_song_from_queue(filename):
    removed_current = False
    while filename in playlist_queue:
        index = playlist_queue.index(filename)
        if index == 0:
            removed_current = True
        playlist_queue.pop(index)
    return removed_current


def delete_song_file(filename):
    filename = os.path.basename(str(filename).strip())
    if not filename or not filename.lower().endswith('.mp4'):
        return False, False, "Invalid filename"

    song_path = os.path.abspath(os.path.join(SONGS_DIR, filename))
    songs_root = os.path.abspath(SONGS_DIR)
    if not song_path.startswith(songs_root + os.sep) and song_path != songs_root:
        return False, False, "Invalid song path"

    if not os.path.exists(song_path):
        return False, False, f"Song not found: {filename}"

    os.remove(song_path)
    lyric_path = os.path.splitext(song_path)[0] + '.lrc'
    if os.path.exists(lyric_path):
        try:
            os.remove(lyric_path)
        except:
            pass
    removed_current = remove_song_from_queue(filename)
    return True, removed_current, filename

# ------------------------------------------
# Flask 路由
# ------------------------------------------
@app.route('/player')
def page_player(): return render_template('player.html')

@app.route('/remote')
def page_remote(): return render_template('remote.html')

@app.route('/admin')
def page_admin(): return render_template('admin.html')

@app.route('/combo')  
def page_combo(): return render_template('combo.html')

@app.route('/')
def page_index(): return render_template('remote.html')

@app.route('/songs/<path:filename>')
def serve_song(filename):
    return send_from_directory(SONGS_DIR, filename)


@app.route('/lyrics/<path:filename>')
def serve_lyrics(filename):
    return send_from_directory(SONGS_DIR, filename)

@app.route('/api/list')
def get_song_list():
    songs = [f for f in os.listdir(SONGS_DIR) if f.lower().endswith('.mp4')]
    return json.dumps(songs) 


@app.route('/api/delete/<path:filename>', methods=['POST'])
def delete_song_api(filename):
    try:
        success, removed_current, result = delete_song_file(filename)
        if not success:
            return json.dumps({'ok': False, 'error': result}), 404

        broadcast_log(f"🗑️ Deleted song: {result}")

        return json.dumps({'ok': True})
    except Exception as exc:
        broadcast_log(f"❌ Failed to delete song via API: {exc}")
        return json.dumps({'ok': False, 'error': str(exc)}), 500


def save_song_lyrics(song_filename, lyrics_text):
    song_filename = os.path.basename(str(song_filename).strip())
    if not song_filename.lower().endswith('.mp4'):
        raise ValueError('Invalid song filename')

    lyric_path = os.path.join(SONGS_DIR, os.path.splitext(song_filename)[0] + '.lrc')
    with open(lyric_path, 'w', encoding='utf-8') as lyric_file:
        lyric_file.write(str(lyrics_text or '').replace('\r\n', '\n').replace('\r', '\n').strip())


def is_youtube_url(url):
    parsed = urlparse(str(url or '').strip())
    return 'youtube.com' in parsed.netloc or 'youtu.be' in parsed.netloc


def format_lrc_timestamp(total_seconds):
    total_seconds = max(0.0, float(total_seconds or 0.0))
    minutes = int(total_seconds // 60)
    seconds = int(total_seconds % 60)
    milliseconds = int(round((total_seconds - int(total_seconds)) * 1000))
    if milliseconds >= 1000:
        minutes += 1
        milliseconds -= 1000
    return f"[{minutes:02d}:{seconds:02d}.{milliseconds:03d}]"


def subtitle_timestamp_to_seconds(timestamp_text):
    cleaned = str(timestamp_text or '').strip().replace(',', '.')
    if not cleaned:
        return None

    parts = cleaned.split(':')
    if len(parts) == 3:
        hours_text, minutes_text, seconds_text = parts
    elif len(parts) == 2:
        hours_text = '0'
        minutes_text, seconds_text = parts
    else:
        return None

    try:
        hours = int(hours_text)
        minutes = int(minutes_text)
        seconds = float(seconds_text)
    except ValueError:
        return None

    return hours * 3600 + minutes * 60 + seconds


def subtitles_to_lrc(subtitle_text):
    blocks = re.split(r'\n\s*\n', str(subtitle_text or '').strip(), flags=re.MULTILINE)
    lyrics_lines = []

    for block in blocks:
        lines = [line.strip('\ufeff').strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue

        timing_index = next((idx for idx, line in enumerate(lines) if '-->' in line), None)
        if timing_index is None:
            continue

        match = re.search(
            r'(?P<start>\d{1,2}:\d{2}(?::\d{2})?[\.,]\d{1,3})\s*-->\s*(?P<end>\d{1,2}:\d{2}(?::\d{2})?[\.,]\d{1,3})',
            lines[timing_index],
        )
        if not match:
            continue

        start_seconds = subtitle_timestamp_to_seconds(match.group('start'))
        if start_seconds is None:
            continue

        lyric_text = ' '.join(lines[timing_index + 1:]).strip()
        if not lyric_text:
            continue

        lyrics_lines.append(f"{format_lrc_timestamp(start_seconds)}{lyric_text}")

    return '\n'.join(lyrics_lines).strip()


def extract_youtube_caption_lyrics(source_url, temp_dir):
    if not is_youtube_url(source_url):
        return None

    subtitle_dir = os.path.join(temp_dir, 'captions')
    os.makedirs(subtitle_dir, exist_ok=True)

    cmd = get_ytdlp_command() + [
        '--skip-download',
        '--write-subs',
        '--write-auto-subs',
        '--sub-langs', 'all',
        '--sub-format', 'vtt',
        '-o', os.path.join(subtitle_dir, 'captions.%(ext)s'),
        source_url,
    ]

    deno_executable = get_deno_executable()
    if deno_executable:
        cmd += ['--js-runtimes', f'deno:{deno_executable}', '--remote-components', 'ejs:github']

    try:
        subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
    except subprocess.CalledProcessError:
        return None

    subtitle_candidates = []
    for ext in ('vtt', 'srt', 'ass'):
        subtitle_candidates.extend(sorted(glob.glob(os.path.join(subtitle_dir, f'*.{ext}'))))

    if not subtitle_candidates:
        return None

    for subtitle_path in subtitle_candidates:
        try:
            with open(subtitle_path, 'r', encoding='utf-8', errors='replace') as subtitle_file:
                subtitle_text = subtitle_file.read()
            timed_lyrics = subtitles_to_lrc(subtitle_text)
            if timed_lyrics:
                return timed_lyrics
        except Exception:
            continue

    return None


def lyrics_file_exists(song_filename):
    lyric_path = os.path.join(SONGS_DIR, os.path.splitext(os.path.basename(song_filename))[0] + '.lrc')
    return os.path.exists(lyric_path)

# ------------------------------------------
# SocketIO 事件處理 & 待播清單
# ------------------------------------------
playlist_queue = []

@socketio.on('add_to_queue')
def handle_add_queue(data):
    filename = data['filename']
    playlist_queue.append(filename)
    
    # 廣播更新所有設備上的歌單畫面
    socketio.emit('update_queue', playlist_queue)
    
    # 如果清單裡面只有剛點的這首歌，代表目前沒有歌在播，立刻開始播放
    if len(playlist_queue) == 1:
        socketio.emit('play_video', {'filename': filename, 'title': filename})

@socketio.on('request_play')
def handle_request_play(data):
    # 舊版遙控器送出的事件名稱，沿用同一套排隊/播放邏輯
    handle_add_queue(data)


@socketio.on('delete_song')
def handle_delete_song(data):
    try:
        success, removed_current, result = delete_song_file(data.get('filename', ''))
        if not success:
            broadcast_log(f"⚠️ {result}")
            return

        broadcast_log(f"🗑️ Deleted song: {result}")
        broadcast_song_list()
        socketio.emit('update_queue', playlist_queue)

        if removed_current:
            if len(playlist_queue) > 0:
                socketio.emit('play_video', {'filename': playlist_queue[0], 'title': playlist_queue[0]})
            else:
                socketio.emit('stop_video')
    except Exception as exc:
        broadcast_log(f"❌ Failed to delete song: {exc}")

@socketio.on('song_ended')
def handle_song_ended():
    if len(playlist_queue) > 0:
        # 移除剛剛唱完的那首歌
        playlist_queue.pop(0) 
        socketio.emit('update_queue', playlist_queue)
        
        # 檢查是否還有下一首
        if len(playlist_queue) > 0:
            next_song = playlist_queue[0]
            socketio.emit('play_video', {'filename': next_song, 'title': next_song})
        else:
            # 沒歌了，停止畫面並回到待機狀態
            socketio.emit('stop_video')

@socketio.on('control')
def handle_control(action):
    if action == 'cut':
        # 按下切歌時，等於強迫觸發「歌曲結束」事件，讓系統自動播下一首
        handle_song_ended()
    else:
        # 其他指令 (例如 pause) 照常發送
        socketio.emit('command', action)

# ------------------------------------------
# (以下原本的音效與下載事件保留不動)
@socketio.on('control_effect')
def handle_effect(data):
    socketio.emit('apply_effect', data)

@socketio.on('change_track')
def handle_track(mode):
    socketio.emit('set_audio', mode)

@socketio.on('seek_to')
def handle_seek_to(data):
    try:
        seconds = float((data or {}).get('seconds', 0))
    except Exception:
        return

    if seconds < 0:
        seconds = 0
    socketio.emit('seek_to', {'seconds': seconds})

is_processing = False
download_queue = []
download_queue_lock = threading.Lock()


def emit_download_status():
    with download_queue_lock:
        processing = is_processing
        queued = len(download_queue)

    status = 'busy' if processing or queued > 0 else 'idle'
    socketio.emit('task_status', {'status': status, 'queued': queued, 'active': processing})


def try_start_next_download():
    global is_processing
    next_job = None

    with download_queue_lock:
        if (not is_processing) and download_queue:
            next_job = download_queue.pop(0)
            is_processing = True

    if next_job is not None:
        threading.Thread(target=run_download_job, args=(next_job,), daemon=True).start()

    emit_download_status()


def run_download_job(job):
    global is_processing

    url = job.get('url', '')
    title = job.get('title', '')
    lyrics = job.get('lyrics', '')

    broadcast_log("=== Starting new job ===")

    try:
        processor = KTVProcessor(log_cb=broadcast_log)
        final_result = processor.process_song(url, title)

        if final_result:
            final_filename, extracted_lyrics = final_result
            lyrics_to_save = extracted_lyrics or (lyrics if lyrics and str(lyrics).strip() else '')
            if lyrics_to_save and str(lyrics_to_save).strip():
                try:
                    save_song_lyrics(final_filename, lyrics_to_save)
                    broadcast_log(f"📝 Lyrics saved for: {final_filename}")
                except Exception as exc:
                    broadcast_log(f"⚠️ Lyrics save failed: {exc}")
            socketio.emit('refresh_list')
    except Exception as exc:
        broadcast_log(f"❌ Process failed: {exc}")
    finally:
        with download_queue_lock:
            is_processing = False

        try_start_next_download()


# ==========================================
# Demucs 獨立進程處理函式
# ==========================================
def _run_demucs_process(input_path, output_dir, log_path):
    """
    這個函式會在一個完全獨立的 Python 進程中執行。
    結束時作業系統會強制清空此進程佔用的 PyTorch 記憶體。
    """
    cmd = [
        sys.executable,
        "-m",
        "demucs",
        "--two-stems",
        "vocals",
        "-o",
        output_dir,
        input_path,
    ]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        with open(log_path, "w", encoding="utf-8") as log_file:
            if result.stdout:
                log_file.write(result.stdout)
            if result.stderr:
                if result.stdout:
                    log_file.write("\n")
                log_file.write(result.stderr)
    except subprocess.CalledProcessError as exc:
        with open(log_path, "w", encoding="utf-8") as log_file:
            if exc.stdout:
                log_file.write(exc.stdout)
            if exc.stderr:
                if exc.stdout:
                    log_file.write("\n")
                log_file.write(exc.stderr)
        raise




@socketio.on('start_download')
def handle_start_download(data):
    url = str((data or {}).get('url', '')).strip()
    title = str((data or {}).get('title', '')).strip()
    lyrics = (data or {}).get('lyrics', '')

    if not url or not title:
        broadcast_log("⚠️ Upload skipped: URL and title are required.")
        return

    with download_queue_lock:
        download_queue.append({'url': url, 'title': title, 'lyrics': lyrics})
        waiting_count = len(download_queue)

    broadcast_log(f"📥 Queued: {title} (waiting in queue: {waiting_count})")
    try_start_next_download()

@socketio.on('update_ytdlp')
def handle_update_ytdlp():
    def run_update():
        broadcast_log("Updating yt-dlp core...")
        try:
            cmd = get_ytdlp_command() + ["-U"]
            result = subprocess.run(cmd, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            broadcast_log(result.stdout)
            if result.stderr: broadcast_log(result.stderr)
            broadcast_log("✅ yt-dlp update finished.")
        except Exception as e:
            broadcast_log(f"❌ Update failed: {str(e)}")

    threading.Thread(target=run_update, daemon=True).start()

def run_server_thread():
    try:
        print("🚀 Starting Flask server...")
        
        # 【關鍵防護】強制關閉 Flask 雞婆的啟動橫幅 (Banner) 與日誌，從根本拔除報錯源頭
        import logging
        from flask import cli
        cli.show_server_banner = lambda *args, **kwargs: None  # 暴力閹割橫幅印出功能
        logging.getLogger('werkzeug').setLevel(logging.ERROR)  # 只允許印出重大錯誤
        
        socketio.run(app, host='0.0.0.0', port=PORT, debug=False, allow_unsafe_werkzeug=True)
    except Exception as e:
        import traceback
        print(f"❌ Server start failed: {e}")
        print(traceback.format_exc())

# ==========================================
# 核心處理類別
# ==========================================
class KTVProcessor:
    def __init__(self, log_cb):
        self.log = log_cb

    def sanitize_filename(self, name):
        return "".join([c for c in name if c not in r'\/:*?"<>|'])

    def normalize_youtube_url(self, url):
        parsed = urlparse(url)
        if "youtube.com" not in parsed.netloc and "youtu.be" not in parsed.netloc:
            return url

        if "youtu.be" in parsed.netloc:
            video_id = parsed.path.strip("/")
            return f"https://www.youtube.com/watch?v={video_id}" if video_id else url

        query = parse_qs(parsed.query)
        video_id = query.get("v", [""])[0]
        if not video_id:
            return url

        clean_query = urlencode({"v": video_id})
        return urlunparse(("https", "www.youtube.com", "/watch", "", clean_query, ""))

    def process_song(self, url, manual_title):
        job_temp_dir = None
        job_succeeded = False
        try:
            safe_title = self.sanitize_filename(manual_title)
            normalized_url = self.normalize_youtube_url(url)
            self.log(f"Target song: {safe_title}")
            if normalized_url != url:
                self.log("Normalized the YouTube URL to improve download stability")

            job_id = str(int(time.time()))
            job_temp_dir = os.path.join(TEMP_BASE_DIR, job_id)
            os.makedirs(job_temp_dir, exist_ok=True)

            temp_input = os.path.join(job_temp_dir, "input.mp4")
            temp_output = os.path.join(job_temp_dir, "output.mp4")

            ffmpeg_executable = get_ffmpeg_executable()
            if ffmpeg_executable is None:
                raise Exception("FFmpeg not found. Please install it or place ffmpeg in the app folder.")

            deno_executable = get_deno_executable()
            separator_log = os.path.join(job_temp_dir, "separator.log")

            self.log("Step 1/4: Downloading video...")
            cmd_dl = get_ytdlp_command() + [
                "--ffmpeg-location", os.path.dirname(ffmpeg_executable), 
                "--force-overwrites",  
                "--no-playlist",       
                "--extractor-args", "youtube:player_client=android,web",
                "-f", "best[ext=mp4]/18/best", 
                "-o", temp_input, 
                normalized_url
            ]
            if deno_executable:
                cmd_dl += ["--js-runtimes", f"deno:{deno_executable}", "--remote-components", "ejs:github"]
            
            try:
                subprocess.run(
                    cmd_dl, check=True, capture_output=True, text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
                )
            except subprocess.CalledProcessError as exc:
                download_output = "\n".join(part for part in [exc.stdout, exc.stderr] if part).strip()
                if download_output:
                    self.log(download_output)
                lowered_output = download_output.lower()
                if "video unavailable" in lowered_output or "restricted" in lowered_output or "private video" in lowered_output:
                    raise Exception("YouTube is blocking this video for your account, workspace, or network. Try a different video or sign in with an account that has access.")
                raise Exception(f"yt-dlp download failed with code {exc.returncode}")

            extracted_lyrics = extract_youtube_caption_lyrics(normalized_url, job_temp_dir)
            if extracted_lyrics:
                self.log("🎬 Extracted YouTube captions for timed lyrics.")

            self.log("Step 2/4: AI vocal separation (Demucs)... (this may take a while)")
            
            # 【終極修復】改用獨立進程執行 Demucs，避免 PyTorch 記憶體殘留。
            import multiprocessing
            p = multiprocessing.Process(target=_run_demucs_process, args=(temp_input, job_temp_dir, separator_log))
            p.start()
            p.join() # 等待進程執行完畢

            if os.path.exists(separator_log):
                with open(separator_log, "r", encoding="utf-8", errors="replace") as log_file:
                    separator_output = log_file.read().strip()
                if separator_output:
                    self.log(separator_output)
            
            if p.exitcode != 0:
                raise Exception(f"Demucs separation failed. Subprocess exited with code {p.exitcode}")
            
            # Demucs 會建立 model/track/stem 的輸出結構
            base_name = os.path.splitext(os.path.basename(temp_input))[0] # 會得到 "input"
            model_name = "htdemucs"
            voc_path = os.path.join(job_temp_dir, model_name, base_name, "vocals.wav")
            acc_path = os.path.join(job_temp_dir, model_name, base_name, "no_vocals.wav")

            if not os.path.exists(voc_path) or not os.path.exists(acc_path):
                raise Exception("Demucs separation failed. Audio stems were not found.")

            self.log("Step 3/4: Merging L/R channels (L: vocal, R: instrumental)...")
            cmd_ffmpeg = [
                ffmpeg_executable,
                "-y",
                "-i", temp_input,
                "-i", voc_path,
                "-i", acc_path,
                "-filter_complex", "[0:a]pan=mono|c0=0.5*FL+0.5*FR[L];[2:a]pan=mono|c0=0.5*FL+0.5*FR[R];[L][R]join=inputs=2:channel_layout=stereo[a]",
                "-map", "0:v",
                "-map", "[a]",
                "-c:v", "copy",
                "-c:a", "aac",
                temp_output,
            ]
            
            subprocess.run(
                cmd_ffmpeg, check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
            )

            self.log(f"Step 4/4: Saving as {safe_title}.mp4")
            final = os.path.join(SONGS_DIR, f"{safe_title}.mp4")
            
            if os.path.exists(final):
                final = os.path.join(SONGS_DIR, f"{safe_title}_{job_id}.mp4")

            shutil.move(temp_output, final)
            
            self.log("✅ Processing complete. The song has been added to the library.")
            job_succeeded = True
            return os.path.basename(final), extracted_lyrics

        except subprocess.CalledProcessError as e:
            self.log(f"❌ Process failed (Code {e.returncode})")
            if e.stdout:
                self.log(e.stdout.strip())
            if e.stderr:
                self.log(e.stderr.strip())
            return None
        except Exception as e:
            self.log(f"❌ Error: {e}")
            return None
        finally:
            if job_succeeded and job_temp_dir and os.path.exists(job_temp_dir):
                try:
                    shutil.rmtree(job_temp_dir, ignore_errors=True)
                except:
                    pass 

# ==========================================
# 本機 GUI 
# ==========================================
class ServerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("KTV Server Status")
        self.geometry("450x500") # 稍微拉高一點放日誌框
        self.configure(bg="#f4f4f9")
        
        tk.Label(self, text="🎤 KTV System Running", font=("Microsoft JhengHei", 20, "bold"), fg="#4CAF50", bg="#f4f4f9").pack(pady=10)
        
        info_frame = tk.Frame(self, bg="white", bd=1, relief="solid")
        info_frame.pack(fill="x", padx=20, pady=5)
        
        self.create_clickable_link(info_frame, "📺 Player (TV)", f"http://{LOCAL_IP}:{PORT}/player", "blue")
        self.create_clickable_link(info_frame, "📱 Remote (Phone)", f"http://{LOCAL_IP}:{PORT}/remote", "#d32f2f")
        self.create_clickable_link(info_frame, "🕹️ Combo (Single PC)", f"http://{LOCAL_IP}:{PORT}/combo", "#9C27B0")
        self.create_clickable_link(info_frame, "⚙️ Admin (Add Songs)", f"http://{LOCAL_IP}:{PORT}/admin", "#F57C00")

        stat_frame = tk.Frame(self, bg="#f4f4f9")
        stat_frame.pack(fill="x", padx=20, pady=5)
        
        self.lbl_count = tk.Label(stat_frame, text="Total songs: loading...", font=("Microsoft JhengHei", 12, "bold"), bg="#f4f4f9")
        self.lbl_count.pack(anchor="w")
        
        self.lbl_size = tk.Label(stat_frame, text="Storage used: loading...", font=("Microsoft JhengHei", 12, "bold"), bg="#f4f4f9")
        self.lbl_size.pack(anchor="w", pady=5)

        # 增加一個實體的 GUI 日誌框，用來接聽攔截到的錯誤訊息
        self.log_txt = tk.Text(self, height=8, state="disabled", bg="#222", fg="#0f0", font=("Consolas", 9))
        self.log_txt.pack(fill="both", expand=True, padx=20, pady=10)
        
        self.update_stats()
        
        # 啟動背景佇列監聽器
        self.check_log_queue()

    def create_clickable_link(self, parent, text_prefix, url, color):
        frame = tk.Frame(parent, bg="white")
        frame.pack(pady=2, anchor="w", padx=10)
        tk.Label(frame, text=f"{text_prefix}: ", font=("Consolas", 11), bg="white").pack(side="left")
        link_lbl = tk.Label(frame, text=url, font=("Consolas", 11, "underline"), fg=color, bg="white", cursor="hand2")
        link_lbl.pack(side="left")
        link_lbl.bind("<Button-1>", lambda e, u=url: webbrowser.open(u))

    def update_stats(self):
        try:
            songs = [f for f in os.listdir(SONGS_DIR) if f.endswith('.mp4')]
            count = len(songs)
            total_size = sum(os.path.getsize(os.path.join(SONGS_DIR, f)) for f in songs)
            size_mb = total_size / (1024 * 1024)
            
            self.lbl_count.config(text=f"🎵 Total songs: {count}")
            self.lbl_size.config(text=f"💾 Storage used: {size_mb:.2f} MB")
        except Exception as e:
            pass
        self.after(5000, self.update_stats)

    def check_log_queue(self):
        """每 100 毫秒檢查一次佇列，把背景的文字寫進 GUI 日誌框"""
        try:
            while not system_log_queue.empty():
                msg = system_log_queue.get_nowait()
                self.log_txt.config(state="normal")
                self.log_txt.insert("end", msg + "\n")
                self.log_txt.see("end")
                self.log_txt.config(state="disabled")
        except Exception:
            pass
        self.after(100, self.check_log_queue)

if __name__ == "__main__":
    # 【關鍵】多進程保護必須放在 if __name__ == "__main__": 的第一行
    multiprocessing.freeze_support()

    if get_ffmpeg_executable() is None:
        try:
            messagebox.showerror("Error", "FFmpeg not found\nPlease place the ffmpeg folder in the app directory")
        except:
            print("FFmpeg not found")
    else:
        t = threading.Thread(target=run_server_thread)
        t.daemon = True
        t.start()
        
        app = ServerApp()
        app.mainloop()