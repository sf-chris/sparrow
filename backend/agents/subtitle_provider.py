"""Small, bounded OpenSubtitles REST adapter; processing stays inside Sparrow."""

import httpx
from urllib.parse import urlparse
from .runtime import ToolError

BASE = "https://api.opensubtitles.com/api/v1"
MAX_SUBTITLE_BYTES = 2 * 1024 * 1024


class SubtitleProvider:
    def __init__(self, settings):
        self.settings = settings

    def headers(self):
        if not self.settings.get("api_key"):
            raise ToolError(
                "Connect a subtitle provider in Server settings, or add a local subtitle file."
            )
        return {
            "Api-Key": self.settings["api_key"],
            "User-Agent": "Sparrow v0.2.0",
            "Accept": "application/json",
        }

    async def search(self, asset, item, language, kind, movie_hash):
        params = {
            "languages": language,
            "type": "episode" if item.media_type.value == "tv" else "movie",
            "order_by": "download_count",
            "order_direction": "desc",
        }
        if item.tmdb_id:
            params["parent_tmdb_id" if item.media_type.value == "tv" else "tmdb_id"] = (
                item.tmdb_id
            )
        else:
            params["query"] = item.title
        if asset.get("season") is not None:
            params.update(
                season_number=asset["season"], episode_number=asset["episode"]
            )
        if movie_hash:
            params["moviehash"] = movie_hash
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                BASE + "/subtitles", headers=self.headers(), params=params
            )
            if response.status_code != 200:
                raise ToolError(
                    "Subtitle search is unavailable. Check the provider connection or try a local subtitle."
                )
            data = response.json()
        result = []
        for entry in data.get("data", [])[:20]:
            value = entry.get("attributes", {})
            feature = value.get("feature_details", {})
            identity_key = (
                "parent_tmdb_id" if item.media_type.value == "tv" else "tmdb_id"
            )
            if item.tmdb_id and str(feature.get(identity_key, "")) != str(item.tmdb_id):
                continue
            if value.get("language") != language:
                continue
            if item.media_type.value == "tv" and (
                feature.get("season_number") != asset.get("season")
                or feature.get("episode_number") != asset.get("episode")
            ):
                continue
            subtitle_kind = (
                "forced"
                if value.get("foreign_parts_only")
                else "sdh" if value.get("hearing_impaired") else "full"
            )
            if subtitle_kind != kind:
                continue
            for file in value.get("files", [])[:1]:
                result.append(
                    {
                        "id": "provider:" + str(file["file_id"]),
                        "source": "provider",
                        "file_id": file["file_id"],
                        "language": language,
                        "kind": subtitle_kind,
                        "hash_match": bool(value.get("moviehash_match")),
                        "title": value.get("release") or "Subtitle provider",
                        "provider_id": entry.get("id"),
                    }
                )
        return sorted(result, key=lambda r: not r["hash_match"])[:3]

    async def download(self, file_id):
        headers = self.headers()
        async with httpx.AsyncClient(timeout=20) as client:
            if self.settings.get("username") and self.settings.get("password"):
                response = await client.post(
                    BASE + "/login",
                    headers=headers,
                    json={
                        "username": self.settings["username"],
                        "password": self.settings["password"],
                    },
                )
                if response.status_code != 200 or not response.json().get("token"):
                    raise ToolError("The subtitle provider account could not sign in.")
                headers = {
                    **headers,
                    "Authorization": "Bearer " + response.json()["token"],
                }
            response = await client.post(
                BASE + "/download",
                headers=headers,
                json={"file_id": file_id, "sub_format": "srt"},
            )
            if response.status_code == 406:
                raise ToolError(
                    "The subtitle provider’s download allowance has been used. Add a local subtitle or try later."
                )
            if response.status_code != 200:
                raise ToolError(
                    "The subtitle provider could not supply this track. Check its account settings."
                )
            link = response.json().get("link", "")
            url = urlparse(link)
            if (
                url.scheme != "https"
                or not url.hostname
                or url.username is not None
                or url.password is not None
                or url.port not in (None, 443)
                or not (
                    url.hostname == "opensubtitles.com"
                    or url.hostname.endswith(".opensubtitles.com")
                )
            ):
                raise ToolError(
                    "The provider returned an unsupported download address."
                )
            chunks = []
            size = 0
            # Account credentials belong only to the API, never the download host.
            async with client.stream("GET", link, follow_redirects=False) as download:
                if download.status_code != 200:
                    raise ToolError(
                        "The subtitle download expired. Search for a fresh copy."
                    )
                async for block in download.aiter_bytes():
                    size += len(block)
                    if size > MAX_SUBTITLE_BYTES:
                        raise ToolError("The supplied subtitle exceeds the 2 MB limit.")
                    chunks.append(block)
        from charset_normalizer import from_bytes

        decoded = from_bytes(b"".join(chunks)).best()
        if decoded is None:
            raise ToolError("The supplied subtitle text could not be decoded.")
        return str(decoded)
