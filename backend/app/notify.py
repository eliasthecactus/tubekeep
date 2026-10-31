"""Optional webhook notifications (ntfy topic URL or any JSON webhook)."""
import logging

import httpx

from . import settings_store

log = logging.getLogger("tubekeep.notify")


async def send(title: str, message: str) -> None:
    url = (settings_store.get("notify_webhook_url") or "").strip()
    if not url:
        return
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            if "ntfy" in url:
                await c.post(url, content=message.encode(), headers={"Title": title, "Tags": "musical_note"})
            else:
                await c.post(url, json={"title": title, "message": message, "text": f"{title}: {message}"})
    except Exception as e:
        log.warning("notification failed: %s", e)
