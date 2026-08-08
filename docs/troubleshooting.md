# Troubleshooting & Environment Setup Guide

This guide provides setup steps, environment checks, and solutions to common operational issues for developers and AI agents running **PromptStudio**.

---

## 1. Environment Requirements

- **OS:** Windows 10/11
- **Python Version:** Python 3.10+ (Tested on Python 3.14)
- **Python Dependencies:** `opencv-python`, `numpy`
- **Ollama Engine:** Ollama for Windows installed with `moondream` model

---

## 2. Ollama Service Setup & Verification

PromptStudio uses Ollama for local multimodal vision inference.

### 2.1 Verify Ollama Service
Check if Ollama is listening on port `11434`:
```powershell
py -c "import urllib.request; print(urllib.request.urlopen('http://localhost:11434/api/tags').read().decode())"
```

### 2.2 Pulling the Vision Model (`moondream`)
If `moondream` is missing, pull it using Ollama CLI:
```powershell
& "~\AppData\Local\Programs\Ollama\ollama.exe" pull moondream
```

### 2.3 Starting Ollama Service (if stopped)
```powershell
& "~\AppData\Local\Programs\Ollama\ollama.exe" serve
```

---

## 3. Common Troubleshooting Scenarios

### Scenario A: `UnicodeEncodeError` in Windows PowerShell Console
- **Symptom:** Python crashes with `charmap codec can't encode character...` when printing emojis.
- **Fix:** Avoid emojis in console print statements or set environment variable:
  ```powershell
  $env:PYTHONUTF8="1"
  ```

### Scenario B: Port 5000 Already in Use
- **Symptom:** `OSError: [WinError 10048] Only one usage of each socket address is normally permitted`.
- **Fix:** `server.py` enables `socketserver.TCPServer.allow_reuse_address = True`. If a stale python process is holding the port, kill it:
  ```powershell
  Get-Process -Name "python" | Stop-Process -Force
  ```

### Scenario C: `ModuleNotFoundError: No module named 'cgi'`
- **Symptom:** Server crashes on Python 3.14+ when importing standard `cgi` module.
- **Fix:** PromptStudio includes a built-in native multipart parser function `parse_multipart_data()` in `server.py` and does not depend on `cgi`.

### Scenario D: Re-generating AI Vision Prompts for Gallery
- **Fix:** To clear cached prompt descriptions and force Ollama Vision to re-analyze all photos, delete the cache file:
  ```powershell
  Remove-Item -Path "$HOME\Pictures\InstagramSaved\prompts_cache.json" -Force
  ```
