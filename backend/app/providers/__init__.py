"""Registry and connection loading for music services."""
from .. import crypto, db
from .apple import AppleMusic
from .base import LIKED, AuthError, Provider, ProviderError
from .spotify import Spotify
from .tidal import Tidal
from .youtube import YouTubeMusic

PROVIDERS: dict[str, type[Provider]] = {
    "youtube": YouTubeMusic, "spotify": Spotify, "apple": AppleMusic, "tidal": Tidal,
}

__all__ = ["PROVIDERS", "LIKED", "AuthError", "Provider", "ProviderError", "load", "save_creds", "create_connection"]


def load(conn_id: int) -> Provider:
    conn = db.row("SELECT * FROM connections WHERE id = ?", [conn_id])
    if not conn:
        raise ProviderError("Connection not found")
    cls = PROVIDERS.get(conn["provider"])
    if not cls:
        raise ProviderError(f"Unknown provider {conn['provider']}")
    creds = db.jload(crypto.decrypt(conn["credentials_enc"]), {}) if conn["credentials_enc"] else {}
    return cls(conn, creds)


def save_creds(p: Provider) -> None:
    """Persist refreshed tokens."""
    if p.creds_changed:
        db.execute("UPDATE connections SET credentials_enc = ? WHERE id = ?",
                   [crypto.encrypt(db.jdump(p.creds)), p.conn["id"]])
        p.creds_changed = False


def mark(conn_id: int, status: str, error: str | None = None) -> None:
    db.execute("UPDATE connections SET status = ?, last_error = ?, last_checked_at = ? WHERE id = ?",
               [status, error, db.now(), conn_id])


def create_connection(provider: str, label: str, creds: dict, account_id: int | None = None) -> int:
    return db.insert("connections", {
        "provider": provider, "label": label, "credentials_enc": crypto.encrypt(db.jdump(creds)) if creds else "",
        "account_id": account_id, "status": "valid", "last_checked_at": db.now(), "created_at": db.now()})
