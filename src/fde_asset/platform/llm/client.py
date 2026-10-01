"""模型网关客户端：OpenAI 兼容接口，谁在后面都行。

密钥只从环境变量读，不进数据库、不进资产库。本地开发可以写在仓库根的
`.env.local`（已 gitignore），服务启动时会把它加载进环境。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

DEFAULT_TIMEOUT = 30.0


def load_env_file(path: Path) -> None:
    """把 .env.local 加载进环境变量；已有的同名变量优先，不覆盖。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


@dataclass
class LlmConfig:
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    timeout: float = DEFAULT_TIMEOUT

    @property
    def usable(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)


def config_from_env(model_override: str = "") -> LlmConfig:
    return LlmConfig(
        base_url=os.environ.get("FDE_ASSET_LLM_BASE_URL", "").rstrip("/"),
        api_key=os.environ.get("FDE_ASSET_LLM_API_KEY", ""),
        model=model_override or os.environ.get("FDE_ASSET_RERANK_MODEL", ""),
        timeout=float(os.environ.get("FDE_ASSET_LLM_TIMEOUT", DEFAULT_TIMEOUT)),
    )


class LlmClient(Protocol):
    def complete_json(self, system: str, user: str) -> dict[str, Any]: ...


class NullLlmClient:
    """没配模型时用它：调用方据此退回关键词排序。"""

    usable = False

    def complete_json(self, system: str, user: str) -> dict[str, Any]:  # pragma: no cover
        raise RuntimeError("没有配置模型网关")


class OpenAICompatibleClient:
    """走 /chat/completions，要求模型只吐 JSON。"""

    def __init__(self, config: LlmConfig) -> None:
        self.config = config

    @property
    def usable(self) -> bool:
        return self.config.usable

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        import httpx

        response = httpx.post(
            f"{self.config.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.config.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            },
            timeout=self.config.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        text = payload["choices"][0]["message"]["content"]
        return _loads(text)


def _loads(text: str) -> dict[str, Any]:
    """模型偶尔会在 JSON 外面裹一层 ```json，这里剥掉再解析。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        cleaned = cleaned.rsplit("```", 1)[0]
    return json.loads(cleaned)


def build_client(model_override: str = "") -> LlmClient:
    config = config_from_env(model_override)
    return OpenAICompatibleClient(config) if config.usable else NullLlmClient()
