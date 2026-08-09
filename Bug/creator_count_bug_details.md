# Creator Count Increasing Bug Details

## Symptoms
The user noticed the creator count in the application increasing due to "rubbish" creator folders being created that:
1. Did not match the `following_list.json`.
2. Sometimes did not appear to be real Instagram handles (or were irrelevant to the user's targeted scraping).

## Root Causes Identified

### 1. Saved / Tagged Posts Feature (`sync_saved_posts`)
When the `sync_saved_posts` feature runs, it downloads posts that the user has saved on Instagram.
- **The Code:** `downloader.py` assigns the folder name using `post.owner_username`.
- **The Issue:** For a saved post, the `owner_username` is the account that *made* the post (e.g., a club like `404clubnotfound.kl` or a photographer). Because of this, a folder is automatically created for that account. These are real Instagram handles, but they are generally not accounts the user explicitly follows, leading to unexpected new creator folders.

### 2. Dummy / Test Folders
Folders such as `test_nonexistent_xyz_ui_check2` and `someone_new_test` were created.
- **The Cause:** These were literal test payloads sent to the backend `POST /api/creator` endpoint during a previous UI testing session. They successfully generated folders and entered the queue, padding the folder count.
- **Resolution:** These were manually deleted and purged from `creator_scrape_queue.json`.

### 3. Internal Application State Folders
Folders prefixed with underscores exist in the archive (`_trash`, `_thumbs`, `_journal`, `_classify`, `_eval`, `_generations`).
- **The Cause:** These are valid backend state directories created by PromptStudio, not creator handles. The user might notice these when inspecting the filesystem directly.

## Recommendations
To prevent unexpected creator folders from popping up when downloading saved feeds, the backend either needs to:
- Be updated to download saved posts into a generic `_saved` folder, OR
- Only create creator folders if the post owner exists in the user's known targets (`following_list.json`).
