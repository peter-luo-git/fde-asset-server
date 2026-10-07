"""把评审相关的事放进平台工作台的待办。

资产中心有自己的通知页，但人找"有什么在等我"是去平台工作台看的。和平台共用账号时，
评审请求、打回、入库这几件事同时在平台待办里开一条；事情了结（评完了、重新提交了、
看过了）就把它收掉。

发不出去不影响评审本身：平台暂时连不上时，资产中心自己的通知页里照样有。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class PlatformInbox:
    def __init__(self, base_url: str, service_key: str, *, client=None) -> None:
        self.base_url = base_url.rstrip("/")
        self._service_key = service_key
        self._client = client

    def _post(self, body: dict) -> None:
        try:
            if self._client is None:
                import httpx

                self._client = httpx.Client(timeout=5.0)
            response = self._client.post(
                self.base_url + "/internal/notify/asset-inbox",
                json=body,
                headers={"X-FDE-Service-Key": self._service_key},
            )
            if response.status_code != 200:
                logger.warning("平台待办没有更新：%s %s", body.get("action"), response.status_code)
        except Exception as exc:  # noqa: BLE001 - 尽力而为，不能因为它让评审失败
            logger.warning("平台待办没有更新：%s", type(exc).__name__)

    def open(
        self,
        usernames: list[str],
        *,
        candidate_id: str,
        event: str,
        title: str,
        body: str = "",
        engagement_slug: str = "",
    ) -> None:
        usernames = list(dict.fromkeys(name for name in usernames if name))
        if not usernames:
            return
        self._post(
            {
                "action": "open",
                "candidate_id": candidate_id,
                "event": event,
                "usernames": usernames,
                "title": title[:255],
                "body": body[:1000],
                "engagement_slug": engagement_slug,
            }
        )

    def resolve(
        self, *, candidate_id: str, event: str = "", usernames: list[str] | None = None
    ) -> None:
        """收掉这份草稿名下的待办。event 是前缀：`review` 收掉所有评审请求，空串收掉全部。"""
        self._post(
            {
                "action": "resolve",
                "candidate_id": candidate_id,
                "event": event,
                "usernames": list(usernames or []),
            }
        )


class NoPlatformInbox:
    """没有和平台共用账号时（本地开发），什么都不做。"""

    def open(self, *_args, **_kwargs) -> None:
        return None

    def resolve(self, *_args, **_kwargs) -> None:
        return None
